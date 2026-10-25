"""The operational routes (§11): ``/livez``, ``/readyz`` and ``/metrics``, kept out of the public schema.

Probers and scrapers reach a replica directly: the orchestrator's prober (a kubelet, compose's healthcheck)
and Prometheus call its address, never through the ingress. Where the app's public host is the internet's
(the receiver's), the operational routes answer those direct callers alone (``DirectOperational``): a request
that came through a proxy is answered 404, as a path that does not exist is, so that no one outside runs the
readiness checks (a database round trip and a query per schema) or reads the metrics.

A request came through a proxy when it carries ``X-Forwarded-For`` (``proxied``): every proxy in front of
the app appends to it (an ALB, an nginx ingress), and no prober or scraper sends it. That holds whatever
proxies the app declares (``LEDGER_RECEIVER_FORWARDED_HOPS``): a proxy declared as none is still a proxy, and
a count that is wrong must not open the routes. A direct caller that writes the header refuses only itself.
"""

from http import HTTPStatus
from typing import Final, final

from fastapi import Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.datastructures import Headers
lazy from fastapi import FastAPI
lazy from prometheus_client import CollectorRegistry

from ledger_platform.http.problems import unrouted
lazy from ledger_platform.http.metrics import AsgiApp, AsgiReceive, AsgiScope, AsgiSend
lazy from ledger_platform.observability.health import Health

OPERATIONAL_PATHS: Final = frozenset({"/livez", "/readyz", "/metrics"})
"""The routes probers and scrapers call every few seconds: not traced, their spans would bury the requests."""
FORWARDED_FOR: Final = "x-forwarded-for"
"""The header every proxy in front of an app appends the address it was called from to."""


def proxied(scope: AsgiScope) -> bool:
    """Whether the request came through a proxy: it carries ``X-Forwarded-For`` (see the module)."""
    return FORWARDED_FOR in Headers(scope=scope)


@final
class DirectOperational:
    """Answers 404 to an operational route asked through a proxy (see the module); an ASGI middleware."""

    __slots__ = ("_app",)

    def __init__(self, app: AsgiApp) -> None:
        self._app = app

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Refuse an operational route asked through a proxy; run anything else."""
        if scope["type"] == "http" and scope.get("path") in OPERATIONAL_PATHS and proxied(scope):
            await unrouted(HTTPStatus.NOT_FOUND)(scope, receive, send)
            return
        await self._app(scope, receive, send)


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
