"""The HTTP application of a service: its routers, operational routes, metrics and RFC 9457 errors.

Every failure leaves as ``application/problem+json``: a ``LedgerError`` with the stable code of its class, a
request that breaks the schema with its problems listed, an ``HTTPException`` (the framework's own 404 and
405 included) with the code of its status, and anything else as a bare ``internal``. A client reads no more
than the code and the title unless the error was written to be read: the ``detail`` of an ``HTTPException``
is boilerplate the title already says, or whatever its author typed (possibly built from the request), so it
stays out (a server error a route raises is logged with it, cut); a service that has something to tell a
client raises a ``LedgerError``, whose message is built inside its class.
"""

import re
from dataclasses import dataclass, field
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

from ledger_kernel.errors import ErrorKind, LedgerError, truncate
lazy from ledger_platform.health import Health

PROBLEM_JSON: Final = "application/problem+json"
_UNMATCHED: Final = "unmatched"
_UNREGISTERED: Final = "unregistered"
_OTHER_METHOD: Final = "other"
_METHODS: Final = frozenset(method.value for method in HTTPMethod)
_FAILED: Final = HTTPStatus.INTERNAL_SERVER_ERROR.value
_STANDARD: Final = {status.value: status for status in HTTPStatus}
_CODES: Final[Mapping[int, str]] = {
    HTTPStatus.BAD_REQUEST: "bad_request",
    HTTPStatus.UNAUTHORIZED: "unauthorized",
    HTTPStatus.FORBIDDEN: "forbidden",
    HTTPStatus.NOT_FOUND: "not_found",
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.NOT_ACCEPTABLE: "not_acceptable",
    HTTPStatus.CONFLICT: "conflict",
    HTTPStatus.GONE: "gone",
    HTTPStatus.PRECONDITION_FAILED: "precondition_failed",
    HTTPStatus.CONTENT_TOO_LARGE: "payload_too_large",
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    HTTPStatus.UNPROCESSABLE_CONTENT: "invalid",
    HTTPStatus.PRECONDITION_REQUIRED: "precondition_required",
    HTTPStatus.TOO_MANY_REQUESTS: "too_many_requests",
    HTTPStatus.INTERNAL_SERVER_ERROR: "internal",
    HTTPStatus.NOT_IMPLEMENTED: "not_implemented",
    HTTPStatus.BAD_GATEWAY: "bad_gateway",
    HTTPStatus.SERVICE_UNAVAILABLE: "unavailable",
    HTTPStatus.GATEWAY_TIMEOUT: "gateway_timeout",
}
"""The codes clients match on, written out: they must not follow the names CPython gives its statuses (3.13
renamed 413, 414, 416 and 422). Where ``ErrorKind`` has a word for it (404, 403, 409, 422, 500, 503) the
code is that word, so a route's ``HTTPException`` and a ``LedgerError`` of the same meaning are one code."""
_NOT_A_WORD: Final = re.compile(r"\W+")
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
_TEXT_LIMIT: Final = 200
"""Text that comes from elsewhere (a validator's message, the detail of an ``HTTPException``) is cut here."""
_QUOTES_THE_INPUT: Final[Mapping[str, str]] = {
    "union_tag_invalid": "Input tag does not match any of the expected tags.",
    "uuid_parsing": "Input should be a valid UUID.",
}
"""Pydantic error types whose message repeats part of what the client sent, and what they say instead.

``union_tag_invalid`` quotes the whole tag; ``uuid_parsing`` quotes the first character that is wrong.
Every other type in pydantic-core 2.50 (``list_all_errors``) is fixed text, a count or a parser's own wording,
except ``value_error`` and ``assertion_error``, which carry what the validator that raised them wrote."""
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
    type: str
    msg: str


class _FieldProblem(TypedDict):
    location: str
    type: str
    message: str


class _Line(NamedTuple):
    """What an HTTP status is called in a problem document."""

    title: str
    code: str


_ISSUES: Final = TypeAdapter(list[_ValidationIssue])


_CLASSES: Final = {
    1: _Line("Informational", "informational"),
    2: _Line("Successful", "successful"),
    3: _Line("Redirection", "redirection"),
    4: _Line("Client Error", "client_error"),
    5: _Line("Server Error", "server_error"),
}
_OTHER_CLASS: Final = _Line("Error", "error")


def _describe(status: int) -> _Line:
    """The title and the stable code of ``status``: ``404`` is ``Not Found`` and ``not_found``.

    The code comes from ``_CODES``, else from the snake-case phrase of a status the standard names, else from
    the class of one it does not (``499`` is a ``client_error``; ``299`` is ``successful``).
    """
    standard = _STANDARD.get(status)
    if standard is None:
        return _CLASSES.get(status // 100, _OTHER_CLASS)
    return _Line(
        standard.phrase, _CODES.get(status) or _NOT_A_WORD.sub("_", standard.phrase.lower()).strip("_")
    )


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
    reported: set[int] = field(default_factory=set[int])
    """The routes found missing from ``templates``, so that each is said once."""

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

        A route that was not there when the app was built (FastAPI mounts a router by reference, so routes
        added to it later reach the app) is ``unregistered``, and said so once: its own path would be a
        wrong label whenever a prefix is missing. Anything no API route took (an unknown path, the
        documentation FastAPI serves itself) is ``unmatched``.
        """
        route = scope.get("route")
        if not isinstance(route, APIRoute):
            return _UNMATCHED
        template = self.templates.get(id(route))
        if template is None:
            self._report_once(route)
            return _UNREGISTERED
        return template

    def _report_once(self, route: APIRoute) -> None:
        if id(route) not in self.reported:
            self.reported.add(id(route))
            _logger.warning("http.route_unregistered", path=route.path)


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


def _field_problems(error: RequestValidationError) -> dict[str, object]:
    """The first ``_MAX_ISSUES`` problems of a request: where, of which type, what to do, how many were cut.

    Nothing of the request is read but where it went wrong: ``input`` is not, a location is cut part by part,
    the message of a pydantic type that quotes the input is replaced (``_QUOTES_THE_INPUT``) and any other is
    cut at ``_TEXT_LIMIT``. What a validator we wrote says in a ``value_error`` is its own to keep free of the
    input, as the platform's errors do with ``excerpt``.
    """
    raised = error.errors()
    issues = _ISSUES.validate_python(raised[:_MAX_ISSUES])
    problems = [
        _FieldProblem(
            location=".".join(truncate(str(part), _LOCATION_PART) for part in issue["loc"]),
            type=issue["type"],
            message=_QUOTES_THE_INPUT.get(issue["type"]) or truncate(issue["msg"], _TEXT_LIMIT),
        )
        for issue in issues
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
        code = _describe(error.status_code).code
        if error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            # The client is told nothing but the code; whoever runs the service is told why, once, bounded.
            _logger.error(
                "http.server_error",
                method=request.method,
                route=labels.of_scope(request.scope),
                status=error.status_code,
                code=code,
                detail=truncate(error.detail, _TEXT_LIMIT),
            )
        if not is_body_allowed_for_status_code(error.status_code):
            return Response(status_code=error.status_code, headers=error.headers)
        return _problem(error.status_code, code, headers=error.headers)

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        _report_defect(request, error, "internal", labels)
        return _problem(HTTPStatus.INTERNAL_SERVER_ERROR, "internal")


def _add_operational_routes(app: FastAPI, health: Health, registry: CollectorRegistry) -> None:
    @app.get("/livez", include_in_schema=False)
    async def livez() -> JSONResponse:
        wedged = health.wedged()
        if wedged:  # a check that will not stop: the process cannot recover by itself, ask for a restart
            return JSONResponse(
                {"status": "wedged", "wedged": list(wedged)}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
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


def create_app(
    *,
    info: ApiInfo,
    health: Health,
    registry: CollectorRegistry,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """A FastAPI application with ``routers`` plus ``/livez``, ``/readyz`` and ``/metrics``.

    Raises ``AmbiguousRouteError`` when one route is mounted under two different templates. Routes must be
    complete before this returns: a route added afterwards, to the app or to a router already included, is
    measured as ``unregistered`` (and logged once), never under a path that may lack a prefix.
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
