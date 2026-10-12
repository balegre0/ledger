"""Fixtures of the platform's tests. Later tasks add real PostgreSQL and NATS here."""

import logging
lazy from collections.abc import Iterator

import pytest
import structlog


def use_the_plain_baseline() -> None:
    """structlog as a service logs to a terminal: no colors, and tracebacks as plain text without locals.

    structlog's own defaults render a traceback with ``rich`` and every local variable of every frame, which
    is slow (seconds for one logged exception) and prints exactly what ``configure_logging`` keeps out of a
    record. A test that logs gets the production rules, and ``capture_logs`` keeps working on top of them.
    """
    structlog.reset_defaults()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False, exception_formatter=structlog.dev.plain_traceback),
        ],
        cache_logger_on_first_use=False,
    )


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: ``configure_logging`` is process-wide state."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    use_the_plain_baseline()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    use_the_plain_baseline()
    structlog.contextvars.clear_contextvars()
