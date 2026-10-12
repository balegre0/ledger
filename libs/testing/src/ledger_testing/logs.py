"""The logging every test starts from: what a service writes to a terminal, without colors or locals."""

import logging
from contextlib import contextmanager
lazy from collections.abc import Generator

import structlog


def use_the_plain_baseline() -> None:
    """Log as a service does to a terminal: no colors, and tracebacks as plain text without locals.

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


@contextmanager
def restored_logging() -> Generator[None]:
    """Run the block from the plain baseline, then leave the process's logging as the block found it.

    ``configure_logging`` changes process-wide state (structlog's configuration, the root logger's
    handlers and level): a test that builds a service must not leave it to the next. Context variables
    bound in the block are cleared, whatever the block raised.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    use_the_plain_baseline()
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        use_the_plain_baseline()
        structlog.contextvars.clear_contextvars()
