"""Structured logs: JSON in production, readable in a terminal; standard-library loggers included.

Two rules keep a record safe and complete wherever it comes from. A traceback never carries local
variables, in either format: a frame that failed to connect holds the database password. And what is true of
the whole process (``service``, ``environment``) is stamped by a processor, not bound in a context variable:
those belong to the calling context, which a thread or an executor does not share.
"""

import logging
import sys
from dataclasses import dataclass
from typing import final

import structlog
lazy from structlog.typing import EventDict, Processor

from ledger_platform.settings import LogFormat
lazy from ledger_platform.settings import ServiceSettings


@final
@dataclass(frozen=True, slots=True)
class _ProcessFields:
    """Stamps every record with the fields of the process. A value given on the call itself wins."""

    service: str
    environment: str

    def __call__(self, logger: object, method_name: str, event_dict: EventDict) -> EventDict:
        del logger, method_name
        event_dict.setdefault("service", self.service)
        event_dict.setdefault("environment", self.environment)
        return event_dict


def _shared_processors(settings: ServiceSettings) -> list[Processor]:
    """What both structlog records and standard-library records go through, in this order."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ProcessFields(settings.service_name, settings.environment),
    ]


def _rendering_processors(log_format: LogFormat) -> list[Processor]:
    """How a record becomes text. Exceptions are plain data or plain text, never with their locals."""
    rendering: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if log_format is LogFormat.JSON:
        rendering += [
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]
    else:
        rendering.append(
            structlog.dev.ConsoleRenderer(colors=False, exception_formatter=structlog.dev.plain_traceback)
        )
    return rendering


def configure_logging(settings: ServiceSettings) -> None:
    """Route structlog and every standard-library logger to stdout in ``settings.log_format``.

    The caller's context variables are left alone: they carry request-scoped values, merged into each record.
    """
    shared = _shared_processors(settings)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared, processors=_rendering_processors(settings.log_format)
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
