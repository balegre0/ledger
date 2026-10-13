"""``ledger-api``: Granian serves the app ``create_app`` builds from ``LEDGER_*`` (§3.3, §13)."""

from importlib.metadata import version
from typing import Final

from granian import Granian
from granian.constants import Interfaces
lazy from fastapi import FastAPI

from ledger_platform.runtime import App, Settings

VERSION: Final = version("ledger-app-api")
WORKER_KILL_TIMEOUT: Final = 30
"""Seconds Granian gives its worker to stop before it kills it: longer than the app's stop budget
(``STOP_BUDGET``), shorter than the orchestrator's grace (40 s: compose ``stop_grace_period``, Kubernetes
``terminationGracePeriodSeconds``)."""


def create_app() -> FastAPI:
    """Granian's factory: the api app of the environment's settings."""
    return App("api", settings=Settings(), version=VERSION).asgi()


def main() -> int:
    """Serve ``create_app`` on ``LEDGER_HTTP_HOST:LEDGER_HTTP_PORT``.

    One Granian worker per replica: an app is scaled by replicas, each sized for one process and its pools.
    """
    settings = Settings()
    Granian(
        "ledger_app_api.main:create_app",
        factory=True,
        interface=Interfaces.ASGI,
        address=settings.http_host,
        port=settings.http_port,
        workers=1,
        respawn_failed_workers=True,
        workers_kill_timeout=WORKER_KILL_TIMEOUT,
        log_access=False,
    ).serve()
    return 0
