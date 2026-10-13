import logging
import threading
from typing import Final, Literal
lazy from collections.abc import AsyncGenerator

import pytest
import structlog
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_span_id, format_trace_id
from pydantic import BaseModel, JsonValue, SecretStr, TypeAdapter, ValidationError
from sqlalchemy import Column, MetaData, Table, Text, insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError
from structlog.testing import capture_logs
lazy from structlog.typing import EventDict

from ledger_platform.config.settings import LogFormat, ServiceSettings
from ledger_platform.data.database import Database
from ledger_platform.observability.logs import Throttled, configure_logging
from ledger_testing.clock import Moment

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


def test_a_record_written_inside_a_span_carries_its_trace_and_span(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings(LogFormat.JSON))
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        structlog.stdlib.get_logger("ledger.test").info("inside")
        logging.getLogger("lib").info("inside too")
    context = span.get_span_context()
    records = lines(capsys.readouterr().out)
    assert len(records) == 2
    for line in records:
        assert (line["trace_id"], line["span_id"]) == (
            format_trace_id(context.trace_id),
            format_span_id(context.span_id),
        )


def test_a_record_written_outside_any_span_carries_no_trace(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings(LogFormat.JSON))
    structlog.stdlib.get_logger("ledger.test").info("outside")
    (line,) = lines(capsys.readouterr().out)
    assert "trace_id" not in line
    assert "span_id" not in line


# ── Exceptions that quote data ──────────────────────────────────────────────────

TAX_ID: Final = "00112345678"
"""A cédula: personal data, which PostgreSQL quotes in the DETAIL of a unique violation."""
TAXPAYERS: Final = Table("taxpayers", MetaData(), Column("tax_id", Text, unique=True))
UNIQUE: Final = "taxpayers_tax_id_key"
WITHHELD: Final = "(message withheld: it can quote values)"
INSERT: Final = "INSERT INTO taxpayers (tax_id) VALUES (%(tax_id)s::VARCHAR)"
"""The statement SQLAlchemy runs to add a taxpayer: code, not data, so the log keeps it."""


class RecordingFailedError(Exception):
    """An error of a service's own, raised while handling a database error: its message is ours."""

    def __init__(self) -> None:
        super().__init__("the payment could not be recorded")


@pytest.fixture
async def taxpayers(database: Database) -> AsyncGenerator[Database]:
    async with database.engine.begin() as connection:
        await connection.run_sync(TAXPAYERS.create)
    yield database
    async with database.engine.begin() as connection:
        await connection.run_sync(TAXPAYERS.drop)


async def unique_violation(database: Database) -> IntegrityError:
    """A real one: the same tax id twice. PostgreSQL's answer quotes it."""
    async with database.transaction(None) as unit:
        await unit.connection.execute(insert(TAXPAYERS).values(tax_id=TAX_ID))
    with pytest.raises(IntegrityError) as violated:
        async with database.transaction(None) as unit:
            await unit.connection.execute(insert(TAXPAYERS).values(tax_id=TAX_ID))
    assert TAX_ID in str(violated.value)
    return violated.value


def logged(error: BaseException, log_format: LogFormat, capsys: pytest.CaptureFixture[str]) -> str:
    configure_logging(settings(log_format))
    structlog.stdlib.get_logger("ledger.test").error("work.failed", exc_info=error)
    return capsys.readouterr().out


def exception_values(output: str) -> dict[str, str]:
    """Each exception of the chain by its type: what a JSON record says it said."""
    (line,) = lines(output)
    stacks = line["exception"]
    assert isinstance(stacks, list)
    values: dict[str, str] = {}
    for stack in stacks:
        assert isinstance(stack, dict)
        kind, value = stack["exc_type"], stack["exc_value"]
        assert isinstance(kind, str)
        assert isinstance(value, str)
        values[kind] = value
    return values


async def test_a_unique_violation_is_logged_as_json_by_its_sqlstate_and_names_without_the_value(
    taxpayers: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    output = logged(await unique_violation(taxpayers), LogFormat.JSON, capsys)
    assert TAX_ID not in output
    named = f"sqlstate=23505 schema=public table=taxpayers constraint={UNIQUE} {WITHHELD}"
    assert exception_values(output) == {
        "IntegrityError": (f"(psycopg.errors.UniqueViolation) {named} [SQL: {INSERT}]"),
        "UniqueViolation": named,
    }


async def test_a_unique_violation_is_logged_to_a_terminal_without_the_value(
    taxpayers: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    output = logged(await unique_violation(taxpayers), LogFormat.CONSOLE, capsys)
    assert TAX_ID not in output
    assert f"UniqueViolation: sqlstate=23505 schema=public table=taxpayers constraint={UNIQUE}" in output
    assert "IntegrityError: (psycopg.errors.UniqueViolation) sqlstate=23505" in output
    assert "Traceback (most recent call last)" in output


@pytest.mark.parametrize("log_format", list(LogFormat))
async def test_a_violation_anywhere_in_the_chain_or_a_group_is_withheld(
    taxpayers: Database, capsys: pytest.CaptureFixture[str], log_format: LogFormat
) -> None:
    """The context of an error raised while handling it, and a member of a group: every copy is withheld."""
    violation = await unique_violation(taxpayers)
    try:
        try:
            raise violation
        except IntegrityError as caught:
            raise RecordingFailedError from caught.__cause__
    except RecordingFailedError as wrapped:
        group = ExceptionGroup("tasks failed", [wrapped, violation])
    output = logged(group, log_format, capsys)
    assert TAX_ID not in output
    assert "the payment could not be recorded" in output  # ours: kept


async def test_a_failed_connection_keeps_what_libpq_says(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A connection attempt names the server and the role, never a row: its words are what ops need."""
    refused = make_url(database_url).set(password="wrong-password").render_as_string(hide_password=False)
    async with Database.open(refused, pool_size=1) as unreachable:
        with pytest.raises(OperationalError) as failed:
            await unreachable.ping()
    output = logged(failed.value, LogFormat.JSON, capsys)
    role = make_url(database_url).username
    assert f'password authentication failed for user "{role}"' in exception_values(output)["OperationalError"]
    assert "wrong-password" not in output


class Payment(BaseModel):
    payer: str
    amount: int


def test_a_validation_error_is_logged_by_its_locations_and_types_without_the_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(ValidationError) as invalid:
        Payment.model_validate({"payer": 7, "amount": TAX_ID + "x"})
    assert TAX_ID in str(invalid.value)  # pydantic quotes the input
    output = logged(invalid.value, LogFormat.JSON, capsys)
    assert TAX_ID not in output
    assert exception_values(output) == {
        "ValidationError": "2 validation errors for Payment: payer (string_type), amount (int_parsing)"
    }


def test_a_request_that_breaks_the_schema_is_logged_without_what_was_sent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    errors = [{"loc": ("body", "payer"), "type": "missing", "msg": "Field required", "input": {"x": TAX_ID}}]
    output = logged(RequestValidationError(errors), LogFormat.CONSOLE, capsys)
    assert TAX_ID not in output
    assert "RequestValidationError: 1 validation errors: body.payer (missing)" in output


def test_validation_problems_beyond_twenty_are_counted_and_unreadable_ones_only_counted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    many = [{"loc": ("body", index), "type": "missing", "input": TAX_ID} for index in range(25)]
    (shown,) = exception_values(logged(ResponseValidationError(many), LogFormat.JSON, capsys)).values()
    assert shown.startswith("25 validation errors: body.0 (missing), body.1 (missing)")
    assert shown.endswith("body.19 (missing), and 5 more")
    odd = exception_values(logged(ResponseValidationError([TAX_ID]), LogFormat.JSON, capsys))
    assert odd == {"ResponseValidationError": "1 validation errors"}


def test_a_statement_error_without_a_driver_error_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    error = StatementError(f"bad value {TAX_ID}", "SELECT 1", {"x": TAX_ID}, None)
    assert exception_values(logged(error, LogFormat.JSON, capsys)) == {
        "StatementError": "(no driver error) [SQL: SELECT 1]"
    }


def test_an_exception_of_another_type_keeps_its_message(capsys: pytest.CaptureFixture[str]) -> None:
    """Ours, or a library's that carries no data: what it says is what the reader needs."""
    shown = exception_values(logged(KeyError("payments-ledger"), LogFormat.JSON, capsys))
    assert shown == {"KeyError": "'payments-ledger'"}


# ── Warnings that recur ─────────────────────────────────────────────────────────


def test_a_recurring_warning_is_logged_once_per_interval_and_says_how_many_were_not() -> None:
    moment = Moment()
    errors = Throttled(structlog.stdlib.get_logger("ledger.test"), "nats.error", every=60, clock=moment)
    with capture_logs() as logs:
        errors.warning(error="ConnectionRefusedError")  # the first: at once
        for second in (2.0, 4.0, 59.9):
            moment.now = second
            errors.warning(error="ConnectionRefusedError")  # within the minute: counted
        moment.now = 60.0
        errors.warning(error="TimeoutError")  # a minute on: logged, with how many were not
        moment.now = 61.0
        errors.warning(error="TimeoutError")
    assert logs == [
        {"event": "nats.error", "log_level": "warning", "error": "ConnectionRefusedError"},
        {"event": "nats.error", "log_level": "warning", "error": "TimeoutError", "repeated": 3},
    ]


def test_settling_says_how_many_went_unlogged_and_the_next_warning_is_logged_at_once() -> None:
    moment = Moment()
    errors = Throttled(structlog.stdlib.get_logger("ledger.test"), "nats.error", every=60, clock=moment)
    with capture_logs() as logs:
        errors.warning()
        errors.warning()
        errors.warning()
        assert errors.settle() == 2  # the outage is over
        assert errors.settle() == 0
        moment.now = 1.0
        errors.warning()  # a new outage
    assert [entry["event"] for entry in logs] == ["nats.error", "nats.error"]
