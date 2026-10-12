"""The HTTP application of a service: its routers, operational routes, metrics and RFC 9457 errors.

Every failure leaves as ``application/problem+json``: a ``LedgerError`` with the stable code of its class, a
request that breaks the schema with its problems listed, an ``HTTPException`` (the framework's own 404 and
405 included) with the code of its status, and anything else as a bare ``internal``. A client reads no more
than the code and the title unless the error was written to be read: the ``detail`` of an ``HTTPException``
is boilerplate the title already says, or whatever its author typed (possibly built from the request), so it
stays out; a service that has something to tell a client raises a ``LedgerError``, whose message is built
inside its class.
"""

from dataclasses import dataclass
from http import HTTPMethod, HTTPStatus
from time import perf_counter
from typing import ClassVar, Final, NamedTuple, Self, TypedDict, final
lazy from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.utils import is_body_allowed_for_status_code
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from pydantic import TypeAdapter
from starlette.exceptions import HTTPException
lazy from fastapi import APIRouter, Request
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, LedgerError
lazy from ledger_platform.health import Health

PROBLEM_JSON: Final = "application/problem+json"
_UNMATCHED: Final = "unmatched"
_OTHER_METHOD: Final = "other"
_METHODS: Final = frozenset(method.value for method in HTTPMethod)
_FAILED: Final = HTTPStatus.INTERNAL_SERVER_ERROR.value
_STANDARD: Final = {status.value: status for status in HTTPStatus}
_STATUS: Final[Mapping[ErrorKind, HTTPStatus]] = {
    ErrorKind.INVALID: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorKind.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorKind.CONFLICT: HTTPStatus.CONFLICT,
    ErrorKind.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorKind.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorKind.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}
_MAX_ISSUES: Final = 20
"""A request can break the schema a million times over; a client is told about this many."""
_LOCATION_PART: Final = 40
"""A location names fields, but a key the client chose is a field name too: it is cut at this length."""
_logger = structlog.stdlib.get_logger(__name__)

# The ASGI protocol, as far as the metrics middleware needs it. Starlette's own aliases hide the values
# behind ``Any``.
type _Scope = MutableMapping[str, object]
type _Message = MutableMapping[str, object]
type _Receive = Callable[[], Awaitable[_Message]]
type _Send = Callable[[_Message], Awaitable[None]]
type _ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


class AmbiguousRouteError(LedgerError):
    """One route reachable under two templates: no single metric label can name it."""

    code: ClassVar[str] = "ambiguous_route"

    def __init__(self, first: str, second: str) -> None:
        super().__init__(f"a route is declared as {first!r} and as {second!r}")


@final
@dataclass(frozen=True, slots=True)
class ApiInfo:
    """What the OpenAPI document says about the service."""

    title: str
    version: str


class _ValidationIssue(TypedDict):
    loc: tuple[str | int, ...]
    msg: str


class _FieldProblem(TypedDict):
    location: str
    message: str


class _Line(NamedTuple):
    """What an HTTP status is called in a problem document."""

    title: str
    code: str


_ISSUES: Final = TypeAdapter(list[_ValidationIssue])


def _describe(status: int) -> _Line:
    """The title and the stable code of ``status``: ``404`` is ``Not Found`` and ``not_found``.

    A status the standard does not name (``499``) still gets an honest answer: the class it belongs to.
    """
    standard = _STANDARD.get(status)
    if standard is not None:
        return _Line(standard.phrase, standard.name.lower())
    if status < HTTPStatus.INTERNAL_SERVER_ERROR:
        return _Line("Client Error", "client_error")
    return _Line("Server Error", "server_error")


@final
@dataclass(frozen=True, slots=True)
class _RouteLabels:
    """The template each route is measured and logged under: the full path a client reaches it by.

    FastAPI reports the route a request took as its own router declared it, without the prefix of the
    ``include_router`` that mounted it, so two children ``/{id}`` under ``/accounts`` and ``/entries`` would
    share one label. The full template of every route comes from the public ``iter_route_contexts``, once,
    when the routes are final. A route object mounted under two templates has no single label: refused.
    """

    templates: Mapping[int, str]
    """By ``id`` of the route object: routes are not hashable, and the app keeps them alive."""

    @classmethod
    def of(cls, app: FastAPI) -> Self:
        templates: dict[int, str] = {}
        for context in iter_route_contexts(app.routes):
            route = context.original_route
            if not isinstance(route, APIRoute):
                continue  # FastAPI's own documentation routes: no request reaches them as a matched route
            template = context.path_format or route.path_format
            first = templates.setdefault(id(route), template)
            if first != template:
                raise AmbiguousRouteError(first, template)
        return cls(templates)

    def of_scope(self, scope: Mapping[str, object]) -> str:
        """The template of the route that took the request, bounded by the code, never by a client.

        A route added to the app after it was built is known by the path it declares. Anything no API route
        took (an unknown path, the documentation FastAPI serves itself) is ``unmatched``.
        """
        route = scope.get("route")
        if isinstance(route, APIRoute):
            return self.templates.get(id(route), route.path)
        return _UNMATCHED


def _method_label(method: object) -> str:
    """The request method, or one shared label for any a client made up: each distinct value is a series."""
    return method if isinstance(method, str) and method in _METHODS else _OTHER_METHOD


@final
class _Measure:
    """Times every HTTP request into a histogram labelled by method, route template and status.

    A plain ASGI middleware rather than ``@app.middleware("http")``: that decorator runs the application in a
    second task, so context variables an endpoint sets do not reach the middleware, and relays every response
    body through a stream.
    """

    __slots__ = ("_app", "_duration", "_labels")

    def __init__(self, app: _ASGIApp, duration: Histogram, labels: _RouteLabels) -> None:
        self._app = app
        self._duration = duration
        self._labels = labels

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = perf_counter()
        status = _FAILED  # what an application that returns without answering amounts to

        async def answer(message: _Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start" and isinstance(code := message["status"], int):
                status = code
            await send(message)

        try:
            await self._app(scope, receive, answer)
        except Exception:
            self._observe(scope, started, _FAILED)  # failed, even if a response had begun
            raise
        self._observe(scope, started, status)

    def _observe(self, scope: _Scope, started: float, status: int) -> None:
        # The router names the route while it dispatches, so it is known only after the application ran.
        self._duration.labels(
            _method_label(scope["method"]), self._labels.of_scope(scope), str(status)
        ).observe(perf_counter() - started)


def _problem(
    status: int,
    code: str,
    *,
    detail: str | None = None,
    extra: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:ledger:error:{code}",
        "title": _describe(status).title,
        "status": int(status),
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    if extra is not None:
        body.update(extra)
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


def _cut(part: str | int) -> str:
    text = str(part)
    return text if len(text) <= _LOCATION_PART else text[:_LOCATION_PART] + "..."


def _field_problems(error: RequestValidationError) -> dict[str, object]:
    """The first ``_MAX_ISSUES`` problems of a request, where they are, and how many were left out.

    Locations are cut and ``input`` is never read: what the client sent does not come back to it.
    """
    raised = error.errors()
    issues = _ISSUES.validate_python(raised[:_MAX_ISSUES])
    problems = [
        _FieldProblem(location=".".join(map(_cut, issue["loc"])), message=issue["msg"]) for issue in issues
    ]
    return {"errors": problems, "omitted_errors": max(len(raised) - _MAX_ISSUES, 0)}


def _report_defect(request: Request, error: Exception, code: str, labels: _RouteLabels) -> None:
    """Log where a defect happened and what it was, never the request: it may carry credentials."""
    _logger.error(
        "http.unhandled_error",
        method=request.method,
        route=labels.of_scope(request.scope),
        code=code,
        exc_info=error,
    )


def _answer_errors_as_problems(app: FastAPI, labels: _RouteLabels) -> None:
    """Every failure leaves as an RFC 9457 document that tells a client no more than it may know."""

    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, error: LedgerError) -> JSONResponse:
        status = _STATUS[error.kind]
        if error.kind is ErrorKind.INTERNAL:
            # A defect of ours: the client gets what any bug gets, the log gets the code and the message.
            _report_defect(request, error, error.code, labels)
            return _problem(status, "internal")
        return _problem(status, error.code, detail=str(error) or None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        del request
        return _problem(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "invalid_request",
            detail="The request does not match the schema.",
            extra=_field_problems(error),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> Response:
        # Starlette's class, not FastAPI's subclass: the router's own 404 and 405 are the former.
        del request
        if not is_body_allowed_for_status_code(error.status_code):
            return Response(status_code=error.status_code, headers=error.headers)
        return _problem(error.status_code, _describe(error.status_code).code, headers=error.headers)

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        _report_defect(request, error, "internal", labels)
        return _problem(HTTPStatus.INTERNAL_SERVER_ERROR, "internal")


def _add_operational_routes(app: FastAPI, health: Health, registry: CollectorRegistry) -> None:
    @app.get("/livez", include_in_schema=False)
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

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


def create_app(
    *,
    info: ApiInfo,
    health: Health,
    registry: CollectorRegistry,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A FastAPI application with ``routers`` plus ``/livez``, ``/readyz`` and ``/metrics``.

    Raises ``AmbiguousRouteError`` when one route is mounted under two different templates.
    """
    app = FastAPI(title=info.title, version=info.version, lifespan=lifespan)
    _add_operational_routes(app, health, registry)
    for router in routers:
        app.include_router(router)
    labels = _RouteLabels.of(app)
    duration = Histogram(
        "http_request_duration_seconds",
        "Time to answer an HTTP request.",
        ["method", "route", "status"],
        registry=registry,
    )
    app.add_middleware(_Measure, duration=duration, labels=labels)
    _answer_errors_as_problems(app, labels)
    return app
