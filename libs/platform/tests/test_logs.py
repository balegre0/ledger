import json
import logging
from typing import Literal

import structlog
from pydantic import SecretStr
lazy import pytest

from ledger_platform.logs import configure_logging
from ledger_platform.settings import LogFormat, ServiceSettings

type Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def settings(log_format: LogFormat, level: Level = "INFO") -> ServiceSettings:
    return ServiceSettings(
        service_name="invoicing",
        database_url=SecretStr("postgresql+psycopg://x"),
        log_format=log_format,
        log_level=level,
    )


def lines(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines()]


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


def explode() -> None:
    raise RuntimeError


def test_exceptions_are_structured(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    try:
        explode()
    except RuntimeError:
        structlog.stdlib.get_logger().exception("failed")
    (line,) = lines(capsys.readouterr().out)
    assert isinstance(line["exception"], list)


def test_lower_levels_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON, "WARNING"))
    structlog.stdlib.get_logger().info("noise")
    logging.getLogger("lib").info("noise")
    assert capsys.readouterr().out == ""


def test_console_logs_are_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.CONSOLE))
    structlog.stdlib.get_logger().info("invoice.issued", number=7)
    output = capsys.readouterr().out
    assert "invoice.issued" in output
    assert "number=7" in output


def test_configuring_again_replaces_the_previous_output(capsys: pytest.CaptureFixture[str]) -> None:
    logger = structlog.stdlib.get_logger("ledger.test")
    configure_logging(settings(LogFormat.CONSOLE))
    logger.info("first")
    capsys.readouterr()
    configure_logging(settings(LogFormat.JSON))
    logger.info("second")
    (line,) = lines(capsys.readouterr().out)
    assert line["event"] == "second"
