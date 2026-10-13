"""The HTTP application of a service: its routers, probes, metrics and RFC 9457 errors.

The operational routes are ``probes``'s, the duration of every request ``metrics``'s and the answers to
failures ``problems``'s; ``create_app`` composes them.

A bug is logged once, by the chassis, and never reaches the ASGI server (which would log it again, message
and all): see ``_Contained``. A failure's log line names the caller and its company when the route's
dependencies learned them (``request_fields``).

Each request is traced by FastAPI's own instrumentation into the provider the app is given, under its route
template: whatever the route does (an event it stores, say) is part of that trace. A failure is recorded by
its type. The rest of FastAPI's telemetry stays off (see ``_telemetry``).

The trace id is the request's id (§9.6): every traced response carries it as ``Request-Id``, and a problem as
``request_id`` too, so a caller must not choose it. By default (``caller_trace="link"``, a public edge) a
caller's ``traceparent`` and ``tracestate`` are taken off the request before it is traced: the request starts
a trace of its own, and its span links to the caller's span, so the two traces still lead to each other. An
app whose every caller is trusted (behind an edge that sets the header itself) may continue the caller's trace
instead (``caller_trace="adopt"``).
"""

from dataclasses import dataclass
from typing import Final, Literal, final, override
lazy from collections.abc import Callable, MutableMapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.trace import SpanContext
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import Histogram
from starlette.datastructures import MutableHeaders
lazy from fastapi import APIRouter
lazy from fastapi.telemetry import TelemetryConfig
lazy from opentelemetry.trace import TracerProvider
lazy from prometheus_client import CollectorRegistry
lazy from starlette.types import ASGIApp, Receive, Scope, Send

from ledger_platform.http.metrics import ROUTE_KEY, UNMATCHED, Measure, RouteLabels
from ledger_platform.http.probes import OPERATIONAL_PATHS, add_probes
from ledger_platform.http.problems import (
    INTERNAL_CODE,
    REQUEST_ID_HEADER,
    current_request_id,
    install_problem_handlers,
)
from ledger_platform.http.request_fields import remembered
lazy from ledger_platform.http.metrics import AsgiApp, AsgiMessage, AsgiReceive, AsgiScope, AsgiSend
lazy from ledger_platform.observability.health import Health

type CallerTrace = Literal["adopt", "link"]
"""What a request does with its caller's ``traceparent``: continue that trace, or start one linked to it."""

_TRACEPARENT: Final = "traceparent"
_TRACESTATE: Final = "tracestate"
_MAX_TRACEPARENT_LENGTH: Final = 55
"""A version 00 ``traceparent``; a longer value is never handed to the propagator (its pattern backtracks)."""
_CALLER_SPAN: Final = "ledger.caller_span"
"""Where the caller's span waits in the request's scope, for the request's own span to link to it."""
_PROPAGATOR: Final = TraceContextTextMapPropagator()
_logger = structlog.stdlib.get_logger(__name__)


@final
@dataclass(frozen=True, slots=True)
class ApiInfo:
    """What the OpenAPI document says about the service."""

    title: str
    version: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Observability:
    """What the app tells about itself: whether it is ready, its metrics and its traces."""

    health: Health
    """The checks ``/readyz`` runs (and whose stuck ones ``/livez`` reports)."""
    registry: CollectorRegistry
    """What ``/metrics`` exports; the app adds the duration of every request to it."""
    tracer_provider: TracerProvider
    """Where the span of every request goes: the service's own provider, never whichever is process-wide."""
    caller_trace: CallerTrace = "link"
    """``link`` at a public edge (the default): a caller's trace is linked to, never continued (see the
    module); ``adopt`` behind a trusted one."""


@final
class _Contained:
    """The outermost layer of the app: no exception of a request reaches the server; each is logged once.

    Starlette answers an exception nothing else handled (with the ``internal`` problem, see ``bug``) and then
    raises it again, for the server to log a second time, message and all. It ends here: logged once, under
    the route ``Measure`` saw it take, and never raised. A response that had begun is left unfinished, so
    that the server closes the connection: a client never takes a cut body for a whole one. Anything that is
    not an ``Exception`` (a cancellation: the client left, the server stops) goes on.
    """

    __slots__ = ("_app",)

    def __init__(self, app: AsgiApp) -> None:
        self._app = app

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        except Exception:
            _logger.exception(
                "http.unhandled_error",
                method=scope["method"],
                route=scope.get(ROUTE_KEY, UNMATCHED),
                code=INTERNAL_CODE,
                **remembered(scope),
            )


def _caller_span(traceparent: str) -> SpanContext | None:
    """The span a caller's ``traceparent`` names, if it names one."""
    if len(traceparent) > _MAX_TRACEPARENT_LENGTH:
        return None
    context = _PROPAGATOR.extract({_TRACEPARENT: traceparent})
    span = trace.get_current_span(context).get_span_context()
    return span if span.is_valid else None


def _without_the_caller_s_trace(scope: AsgiScope) -> None:
    """Take the caller's trace headers off the request, keeping the span they name for a link."""
    headers = MutableHeaders(scope=scope)
    caller = headers.get(_TRACEPARENT)
    del headers[_TRACEPARENT]
    del headers[_TRACESTATE]
    span = None if caller is None else _caller_span(caller)
    if span is not None:
        scope[_CALLER_SPAN] = span


@final
class _Named:
    """Inside the request's span: link it to the caller's, and name the request on its answer.

    The answer names its request in ``Request-Id``.

    Every response that starts while the request is traced carries the trace id, unless it names its request
    already (a problem does). The operational routes are not traced, so they carry none.
    """

    __slots__ = ("_app",)

    def __init__(self, app: AsgiApp) -> None:
        self._app = app

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        caller = scope.get(_CALLER_SPAN)
        if isinstance(caller, SpanContext):
            trace.get_current_span().add_link(caller)
        request_id = current_request_id()

        async def named(message: AsgiMessage) -> None:
            if request_id is not None and message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        await self._app(scope, receive, named)


@final
class _ChassisApp(FastAPI):
    """FastAPI, with ``_Contained`` around everything Starlette builds: its error middleware included.

    With ``caller_trace="link"`` a request loses its caller's trace headers before FastAPI traces it.
    """

    caller_trace: CallerTrace = "link"

    @override
    def build_middleware_stack(self) -> ASGIApp:
        return _Contained(_Named(super().build_middleware_stack()))

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.caller_trace == "link":
            _without_the_caller_s_trace(scope)
        await super().__call__(scope, receive, send)


def _telemetry(provider: TracerProvider) -> TelemetryConfig:
    """FastAPI's own OpenTelemetry, kept to the one thing the chassis wants of it: the span of each request.

    Into ``provider``, so that a service's spans never depend on which provider is process-wide. FastAPI's
    logs stay off: they would record every unhandled exception a second time, its message and traceback
    included, beside the chassis's own log line. Its metrics stay off: Prometheus measures every request
    already. It never adds exporters from the environment (``FASTAPI_OTEL_AUTO_CONFIGURE``): they would
    export past the redaction the chassis gives every exporter of its own. And it leaves the operational
    routes alone.
    """
    return {
        "tracer_provider": provider,
        "logs": False,
        "metrics": False,
        "auto_configure": False,
        "exclude": _is_operational,
    }


def _is_operational(scope: MutableMapping[str, object]) -> bool:
    """Whether the request is for a route of ``OPERATIONAL_PATHS``: FastAPI does not trace those."""
    return scope.get("path") in OPERATIONAL_PATHS


def create_app(
    *,
    info: ApiInfo,
    observability: Observability,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A FastAPI application with ``routers`` plus ``/livez``, ``/readyz`` and ``/metrics``, traced.

    Raises ``AmbiguousRouteError`` when one route is mounted under two different templates. Routes must be
    complete before this returns: a route added afterwards, to the app or to a router already included, is
    measured as ``unregistered`` (and logged once), never under a path that may lack a prefix.
    """
    app = _ChassisApp(
        title=info.title,
        version=info.version,
        lifespan=lifespan,
        telemetry=_telemetry(observability.tracer_provider),
    )
    app.caller_trace = observability.caller_trace
    add_probes(app, observability.health, observability.registry)
    for router in routers:
        app.include_router(router)
    labels = RouteLabels.of(app)
    duration = Histogram(
        "ledger_http_request_duration_seconds",
        "Time to answer an HTTP request.",
        ["method", "route", "status"],
        registry=observability.registry,
    )
    app.add_middleware(Measure, duration=duration, labels=labels)
    install_problem_handlers(app, route_of=labels.of_scope)
    return app
