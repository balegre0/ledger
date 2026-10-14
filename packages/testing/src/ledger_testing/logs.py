"""The logging every test starts from: what an app writes to a terminal, without colors or locals."""

import logging
from contextlib import contextmanager
lazy from collections.abc import Generator

import structlog


def use_the_plain_baseline() -> None:
    """No colors, no locals; redaction is ``configure_logging``'s own, tested in the platform."""
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
    handlers and level): a test that builds an app must not leave it to the next. Context variables
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
