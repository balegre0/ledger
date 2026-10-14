"""The operational routes (§11): ``/livez``, ``/readyz`` and ``/metrics``, kept out of the public schema."""

from http import HTTPStatus
from typing import Final

from fastapi import Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
lazy from fastapi import FastAPI
lazy from prometheus_client import CollectorRegistry

lazy from ledger_platform.observability.health import Health

OPERATIONAL_PATHS: Final = frozenset({"/livez", "/readyz", "/metrics"})
"""The routes probers and scrapers call every few seconds: not traced, their spans would bury the requests."""


def add_probes(app: FastAPI, health: Health, registry: CollectorRegistry) -> None:
    """Add liveness, readiness (``health``'s checks) and metrics (``registry``'s) to ``app``."""

    @app.get("/livez", include_in_schema=False)
    async def livez() -> JSONResponse:
        wedged, dead = health.wedged(), health.dead()
        if wedged or dead:  # a check that will not stop, a part that is gone: only a restart helps
            return JSONResponse(
                {"status": "unrecoverable", "wedged": list(wedged), "dead": list(dead)},
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        checks = await health.run()
        ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
