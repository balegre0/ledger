"""Structured logs: JSON in production, readable in a terminal; standard-library loggers included."""

import logging
import sys

import structlog

from ledger_platform.settings import LogFormat
lazy from ledger_platform.settings import ServiceSettings


def configure_logging(settings: ServiceSettings) -> None:
    """Route structlog and every standard-library logger to stdout in ``settings.log_format``."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    rendering: list[structlog.typing.Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if settings.log_format is LogFormat.JSON:
        rendering += [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        rendering.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processors=rendering))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    structlog.contextvars.bind_contextvars(service=settings.service_name, environment=settings.environment)
