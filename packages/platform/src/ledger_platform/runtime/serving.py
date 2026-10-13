"""The HTTP server's own logs (Granian's), in the app's format (§11).

Granian logs from its main process and from its worker through two standard-library loggers, each with a plain
text handler of its own by default. ``serving_logs`` configures the process's logs as the app does and returns
the ``log_dictconfig`` that takes those handlers away, so the server's records reach the root logger and come
out as the app's lines (JSON in a deployment). The access log stays off: every request is logged and measured
by the app itself.
"""

from typing import Final

from ledger_platform.observability.logs import configure_logging
lazy from ledger_platform.config.settings import Settings

SERVER_LOGGERS: Final = ("_granian", "granian.access")
"""Granian's loggers: its server's records and its access log."""


def serving_logs(settings: Settings, *, service: str) -> dict[str, object]:
    """Configure this process's logs as ``service``'s; return what Granian configures its loggers with."""
    configure_logging(settings, service=service)
    return {
        "loggers": {
            name: {"handlers": [], "propagate": True, "level": settings.log_level} for name in SERVER_LOGGERS
        }
    }
