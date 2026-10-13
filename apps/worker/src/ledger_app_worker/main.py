"""``ledger-worker``: Granian serves the app ``create_app`` builds from ``LEDGER_*`` (§3.3, §13).

Stopping. On SIGTERM Granian stops accepting, lets the requests in flight finish, then runs the app's
shutdown, which takes at most ``STOP_BUDGET`` (25 s); it kills its worker ``WORKER_KILL_TIMEOUT`` (30 s) after
the signal, whatever is still running. Both share that window: a request still running 5 s after the signal
eats into the app's stop, and Granian bounds no request's time. Until a request deadline exists (the ingress's
timeout, v2-M6), routes answer well within 5 s, and long work is a job, never a request. The orchestrator
waits 40 s before its own kill.

Logs. Granian's records go through the platform's logging (``serving_logs``), in the app's format; its access
log is off (the app logs and measures every request). A worker that dies is not respawned: the process exits
and the orchestrator restarts it, with its backoff.
"""

from importlib.metadata import version
from typing import Final

from granian import Granian
from granian.constants import Interfaces
from granian.log import LogLevels
lazy from fastapi import FastAPI

from ledger_platform.runtime import App, Settings, serving_logs

VERSION: Final = version("ledger-app-worker")
WORKER_KILL_TIMEOUT: Final = 30
"""Seconds Granian gives its worker to stop before it kills it: longer than the app's stop budget
(``STOP_BUDGET``), shorter than the orchestrator's grace (40 s: compose ``stop_grace_period``, Kubernetes
``terminationGracePeriodSeconds``)."""


def create_app() -> FastAPI:
    """Granian's factory: the worker app of the environment's settings."""
    return App("worker", settings=Settings(), version=VERSION).asgi()


def main() -> int:
    """Serve ``create_app`` on ``LEDGER_HTTP_HOST:LEDGER_HTTP_PORT``.

    One Granian worker per replica: an app is scaled by replicas, each sized for one process and its pools.
    """
    settings = Settings()
    log_dictconfig = serving_logs(settings, service="ledger-worker")
    Granian(
        "ledger_app_worker.main:create_app",
        factory=True,
        interface=Interfaces.ASGI,
        address=settings.http_host,
        port=settings.http_port,
        workers=1,
        respawn_failed_workers=False,
        workers_kill_timeout=WORKER_KILL_TIMEOUT,
        log_access=False,
        log_level=LogLevels(settings.log_level.lower()),
        log_dictconfig=log_dictconfig,
    ).serve()
    return 0
