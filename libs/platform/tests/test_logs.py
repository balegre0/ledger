import logging
import threading
from typing import Final, Literal

import pytest
import structlog
from pydantic import JsonValue, SecretStr, TypeAdapter
lazy from structlog.typing import EventDict

from ledger_platform.logs import configure_logging
from ledger_platform.settings import LogFormat, ServiceSettings

type Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

RECORD: Final = TypeAdapter(dict[str, JsonValue])
PASSWORD: Final = "hunter2-correct-horse"
FAILURE: Final = "connection refused"


def settings(log_format: LogFormat, level: Level = "INFO") -> ServiceSettings:
    return ServiceSettings(
        service_name="invoicing",
        database_url=SecretStr("postgresql+psycopg://x"),
        log_format=log_format,
        log_level=level,
    )


def lines(captured: str) -> list[dict[str, JsonValue]]:
    return [RECORD.validate_json(line) for line in captured.splitlines()]


def connect(password: str) -> None:
    """Fails like a driver does: the message names the host, the frame holds the whole URL."""
    url = f"postgresql+psycopg://app:{password}@db/ledger"
    message = f"{FAILURE}: {url.partition('@')[2]}"
    raise RuntimeError(message)


def logged_exception(logger: structlog.stdlib.BoundLogger | logging.Logger) -> None:
    try:
        connect(PASSWORD)
    except RuntimeError:
        logger.exception("failed")


def only_exception(line: dict[str, JsonValue]) -> dict[str, JsonValue]:
    exceptions = line["exception"]
    assert isinstance(exceptions, list)
    (exception,) = exceptions
    assert isinstance(exception, dict)
    return exception


def test_json_logs_carry_the_service_level_and_time(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    structlog.stdlib.get_logger("ledger.test").info("invoice.issued", number=7)
    (line,) = lines(capsys.readouterr().out)
    assert line["event"] == "invoice.issued"
    assert line["number"] == 7
    assert (line["service"], line["environment"], line["level"]) == ("invoicing", "local", "info")
    assert str(line["timestamp"]).endswith("Z")


def test_standard_library_loggers_are_json_too(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    logging.getLogger("sqlalchemy.engine").warning("pool exhausted")
    (line,) = lines(capsys.readouterr().out)
    assert (line["event"], line["level"], line["logger"]) == (
        "pool exhausted",
        "warning",
        "sqlalchemy.engine",
    )


@pytest.mark.parametrize(
    "logger",
    [structlog.stdlib.get_logger("ledger.test"), logging.getLogger("lib")],
    ids=["structlog", "standard-library"],
)
def test_exceptions_are_structured_and_never_carry_locals(
    capsys: pytest.CaptureFixture[str], logger: structlog.stdlib.BoundLogger | logging.Logger
) -> None:
    configure_logging(settings(LogFormat.JSON))
    logged_exception(logger)
    output = capsys.readouterr().out
    (line,) = lines(output)
    exception = only_exception(line)
    assert exception["exc_type"] == "RuntimeError"
    assert exception["exc_value"] == f"{FAILURE}: db/ledger"
    frames = exception["frames"]
    assert isinstance(frames, list)
    assert frames
    assert all(isinstance(frame, dict) and "locals" not in frame for frame in frames)
    assert PASSWORD not in output


def test_lower_levels_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON, "WARNING"))
    structlog.stdlib.get_logger().info("noise")
    logging.getLogger("lib").info("noise")
    assert capsys.readouterr().out == ""


def test_dropped_records_are_not_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    processed: list[str] = []

    def spy(logger: object, method_name: str, event_dict: EventDict) -> EventDict:
        del logger
        processed.append(method_name)
        return event_dict

    monkeypatch.setattr(structlog.stdlib, "add_log_level", spy)
    configure_logging(settings(LogFormat.JSON, "WARNING"))
    logger = structlog.stdlib.get_logger()
    logger.info("noise")
    logger.warning("signal")
    assert processed == ["warning"]


def test_console_logs_are_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.CONSOLE))
    structlog.stdlib.get_logger().info("invoice.issued", number=7)
    output = capsys.readouterr().out
    assert "invoice.issued" in output
    assert "number=7" in output


def test_console_exceptions_are_plain_text_without_locals(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.CONSOLE))
    logged_exception(structlog.stdlib.get_logger())
    output = capsys.readouterr().out
    assert f"RuntimeError: {FAILURE}: db/ledger" in output
    assert "\x1b" not in output
    assert PASSWORD not in output


def test_configuring_again_replaces_the_previous_output(capsys: pytest.CaptureFixture[str]) -> None:
    logger = structlog.stdlib.get_logger("ledger.test")
    configure_logging(settings(LogFormat.CONSOLE))
    logger.info("first")
    capsys.readouterr()
    configure_logging(settings(LogFormat.JSON))
    logger.info("second")
    (line,) = lines(capsys.readouterr().out)
    assert line["event"] == "second"


def test_every_thread_logs_the_service_and_environment(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))

    def work() -> None:
        structlog.stdlib.get_logger("ledger.worker").info("from.structlog")
        logging.getLogger("lib").warning("from.standard.library")

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    from_structlog, from_standard_library = lines(capsys.readouterr().out)
    assert (from_structlog["service"], from_structlog["environment"]) == ("invoicing", "local")
    assert (from_standard_library["service"], from_standard_library["environment"]) == ("invoicing", "local")


def test_request_scoped_values_are_merged_and_the_callers_context_is_left_alone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    structlog.contextvars.bind_contextvars(request_id="r-1")
    configure_logging(settings(LogFormat.JSON))
    structlog.stdlib.get_logger("ledger.test").info("invoice.issued")
    (line,) = lines(capsys.readouterr().out)
    assert line["request_id"] == "r-1"
    assert structlog.contextvars.get_contextvars() == {"request_id": "r-1"}
