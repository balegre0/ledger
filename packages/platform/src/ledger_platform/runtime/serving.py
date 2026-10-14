"""Serving an app over HTTP (§3.3, §13): the one Granian setup of the roles that serve (``api``, ``worker``).

An app's console script calls ``serve(role)``; Granian then imports this module's factory of the role (``api``
or ``worker``) in its worker process, and the factory builds the ``App`` from ``LEDGER_*``. One Granian worker
per replica: an app is scaled by replicas, each sized for one process and its pools.

Stopping. On SIGTERM Granian stops accepting, lets the requests in flight finish, then runs the app's
shutdown, which takes at most ``STOP_BUDGET`` seconds; it kills its worker ``WORKER_KILL_TIMEOUT`` seconds
after the signal, whatever is still running, and the orchestrator kills the container ``ORCHESTRATOR_GRACE``
seconds after it (compose ``stop_grace_period``, Kubernetes ``terminationGracePeriodSeconds``). A request
still running after the signal eats into the app's stop, and Granian bounds no request's time: until a request
deadline exists (the ingress's timeout, v2-M6), routes answer well within 5 s, and long work is a job, never a
request. A worker that dies is not respawned: the process exits and the orchestrator restarts it, with its
backoff.

Logs. Granian logs from its main process and from its worker through two standard-library loggers, each with
a plain text handler of its own by default. ``serving_logs`` configures the process's logs as the app does and
returns the ``log_dictconfig`` that takes those handlers away, so the server's records reach the root logger
and come out as the app's lines (JSON in a deployment). The access log stays off: every request is logged and
measured by the app itself.
"""

import math
from importlib.metadata import version
from typing import Final, Literal
lazy from collections.abc import Mapping

from granian import Granian
from granian.constants import Interfaces
from granian.log import LogLevels
lazy from fastapi import FastAPI

from ledger_platform.config.settings import Settings
from ledger_platform.observability.logs import configure_logging
from ledger_platform.runtime.app import STOP_BUDGET, App

type ServedRole = Literal["api", "worker"]
"""The roles an HTTP server serves; ``migrate`` runs once and exits."""

SERVER_LOGGERS: Final = ("_granian", "granian.access")
"""Granian's loggers: its server's records and its access log."""
ORCHESTRATOR_GRACE: Final = 40
"""Seconds the orchestrator waits after SIGTERM before it kills the container."""
KILL_MARGIN: Final = 5
"""Seconds Granian waits beyond the app's stop budget before it kills its worker."""
WORKER_KILL_TIMEOUT: Final = math.ceil(STOP_BUDGET) + KILL_MARGIN
"""Seconds Granian gives its worker to stop: the app's whole stop budget and a margin, inside the grace."""
_FACTORIES: Final[Mapping[ServedRole, str]] = {"api": f"{__name__}:api", "worker": f"{__name__}:worker"}


def serving_logs(settings: Settings, *, service: str) -> dict[str, object]:
    """Configure this process's logs as ``service``'s; return what Granian configures its loggers with."""
    configure_logging(settings, service=service)
    return {
        "loggers": {
            name: {"handlers": [], "propagate": True, "level": settings.log_level} for name in SERVER_LOGGERS
        }
    }


def _served(role: ServedRole) -> FastAPI:
    """The app of ``role`` from the environment's settings, versioned as its app's distribution."""
    return App(role, settings=Settings(), version=version(f"ledger-app-{role}")).asgi()


def api() -> FastAPI:
    """Granian's factory of the ``api`` app."""
    return _served("api")


def worker() -> FastAPI:
    """Granian's factory of the ``worker`` app."""
    return _served("worker")


def serve(role: ServedRole) -> int:
    """Serve ``role``'s app on ``LEDGER_HTTP_HOST:LEDGER_HTTP_PORT`` until the process stops (see the module).

    The exit status: 0 once Granian has stopped. Settings that cannot work raise before anything listens.
    """
    settings = Settings()
    Granian(
        _FACTORIES[role],
        factory=True,
        interface=Interfaces.ASGI,
        address=settings.http_host,
        port=settings.http_port,
        workers=1,
        respawn_failed_workers=False,
        workers_kill_timeout=WORKER_KILL_TIMEOUT,
        log_access=False,
        log_level=LogLevels(settings.log_level.lower()),
        log_dictconfig=serving_logs(settings, service=f"ledger-{role}"),
    ).serve()
    return 0
