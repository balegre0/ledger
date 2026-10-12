"""The HTTP application of a service: its routers, operational routes, metrics and RFC 9457 errors."""

from http import HTTPMethod, HTTPStatus
from time import perf_counter
from typing import Final, TypedDict, final
lazy from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from pydantic import TypeAdapter
lazy from fastapi import APIRouter, Request
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, LedgerError
lazy from ledger_platform.health import Health

PROBLEM_JSON: Final = "application/problem+json"
_UNMATCHED: Final = "unmatched"
_OTHER_METHOD: Final = "other"
_METHODS: Final = frozenset(method.value for method in HTTPMethod)
_FAILED: Final = HTTPStatus.INTERNAL_SERVER_ERROR.value
_STATUS: Final[Mapping[ErrorKind, HTTPStatus]] = {
    ErrorKind.INVALID: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorKind.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorKind.CONFLICT: HTTPStatus.CONFLICT,
    ErrorKind.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorKind.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorKind.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}
_logger = structlog.stdlib.get_logger(__name__)

# The ASGI protocol, as far as the metrics middleware needs it. Spelled out here because Starlette's own
# aliases live in a package this one does not declare, and because they hide the values behind ``Any``.
type _Scope = MutableMapping[str, object]
type _Message = MutableMapping[str, object]
type _Receive = Callable[[], Awaitable[_Message]]
type _Send = Callable[[_Message], Awaitable[None]]
type _ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


class _ValidationIssue(TypedDict):
    loc: tuple[str | int, ...]
    msg: str


class _FieldProblem(TypedDict):
    location: str
    message: str


_ISSUES: Final = TypeAdapter(list[_ValidationIssue])


def _template(scope: Mapping[str, object]) -> str:
    """The path of the API route that took the request, as declared: ``/items/{item_id}``.

    Bounded by the code, never by what a client sends. Anything no API route took (an unknown path, the
    documentation FastAPI serves itself) is ``unmatched``.
    """
    route = scope.get("route")
    return route.path if isinstance(route, APIRoute) else _UNMATCHED


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

    __slots__ = ("_app", "_duration")

    def __init__(self, app: _ASGIApp, duration: Histogram) -> None:
        self._app = app
        self._duration = duration

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
        self._duration.labels(_method_label(scope["method"]), _template(scope), str(status)).observe(
            perf_counter() - started
        )


def _problem(
    status: HTTPStatus, code: str, detail: str | None = None, errors: list[_FieldProblem] | None = None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:ledger:error:{code}",
        "title": status.phrase,
        "status": status.value,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status.value, media_type=PROBLEM_JSON)


def _report_defect(request: Request, error: Exception) -> None:
    """Log where a defect happened and what it was, never the request: it may carry credentials."""
    _logger.error(
        "http.unhandled_error", method=request.method, route=_template(request.scope), exc_info=error
    )


def _answer_errors_as_problems(app: FastAPI) -> None:
    """Every failure leaves as an RFC 9457 document that tells a client no more than it may know."""

    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, error: LedgerError) -> JSONResponse:
        status = _STATUS[error.kind]
        if error.kind is ErrorKind.INTERNAL:
            _report_defect(request, error)  # a defect of ours: its message is for the log, not the client
            return _problem(status, error.code)
        return _problem(status, error.code, str(error) or None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        del request
        issues = _ISSUES.validate_python(error.errors())
        errors = [
            _FieldProblem(location=".".join(map(str, issue["loc"])), message=issue["msg"]) for issue in issues
        ]
        return _problem(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "invalid_request",
            "The request does not match the schema.",
            errors,
        )

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        _report_defect(request, error)
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
    title: str,
    version: str,
    health: Health,
    registry: CollectorRegistry,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A FastAPI application with ``routers`` plus ``/livez``, ``/readyz`` and ``/metrics``.

    Metrics name a route by the path its own router declares, and FastAPI leaves out of that path the prefix
    given to ``include_router``: put the prefix on the ``APIRouter`` that owns the routes.
    """
    app = FastAPI(title=title, version=version, lifespan=lifespan)
    duration = Histogram(
        "http_request_duration_seconds",
        "Time to answer an HTTP request.",
        ["method", "route", "status"],
        registry=registry,
    )
    app.add_middleware(_Measure, duration=duration)
    _answer_errors_as_problems(app)
    _add_operational_routes(app, health, registry)
    for router in routers:
        app.include_router(router)
    return app
