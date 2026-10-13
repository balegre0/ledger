"""RFC 9457 problem documents (§9.6): how every failure of a request is answered.

Every failure leaves as ``application/problem+json``: a ``LedgerError`` with the stable code of its class, a
request that breaks the schema with its problems listed, an ``HTTPException`` (the framework's own 404 and
405 included) with the code of its status, and anything else as a bare ``internal``. A client reads no more
than the code and the title unless the error was written to be read: the ``detail`` of an ``HTTPException``
is boilerplate the title already says, or whatever its author typed (possibly built from the request), so it
stays out (a server error a route raises is logged with it, cut); a service that has something to tell a
client raises a ``LedgerError``, whose message is built inside its class.

A ``LedgerError`` answers the status of its kind (§9.6), except a ``BadRequestError``: a request the HTTP
layer refuses before reading its body (a missing ``Ledger-Mode``, say) answers 400, its kind still
``INVALID``.

A failure's log line names the route (``route_of``, the app's route labels) and the caller and its company
when the route's dependencies learned them (``request_fields``).
"""

import re
from http import HTTPStatus
from typing import ClassVar, Final, NamedTuple, TypedDict
lazy from collections.abc import Callable, Mapping

import structlog
from fastapi import Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from starlette.exceptions import HTTPException
lazy from fastapi import FastAPI, Request

from ledger_kernel.errors import ErrorKind, InvalidInputError, LedgerError, described, truncate
from ledger_platform.http.metrics import declared_route
from ledger_platform.http.request_fields import remembered

PROBLEM_JSON: Final = "application/problem+json"
INTERNAL_CODE: Final = "internal"
"""The code of every defect: a client learns nothing else of it."""
_STANDARD: Final = {status.value: status for status in HTTPStatus}
_CODES: Final[Mapping[int, str]] = {
    HTTPStatus.BAD_REQUEST: "bad_request",
    HTTPStatus.UNAUTHORIZED: "unauthenticated",
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
renamed 413, 414, 416 and 422). Where ``ErrorKind`` has a word for it (404, 401, 403, 409, 422, 500, 503) the
code is that word, so a route's ``HTTPException`` and a ``LedgerError`` of the same meaning are one code."""
_NOT_A_WORD: Final = re.compile(r"\W+")
_STATUS: Final[Mapping[ErrorKind, HTTPStatus]] = {
    ErrorKind.INVALID: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorKind.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorKind.CONFLICT: HTTPStatus.CONFLICT,
    ErrorKind.UNAUTHENTICATED: HTTPStatus.UNAUTHORIZED,
    ErrorKind.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorKind.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorKind.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}
_CHALLENGE: Final = 'Bearer realm="ledger"'
"""What a 401 tells a client to send (RFC 6750): a bearer token. The problem's code says what was wrong."""
_REFUSED_CODE: Final = "invalid_token"
"""The one code a challenge repeats (RFC 6750 section 3.1): a token was sent, and it cannot be trusted. A
request without one gets the bare challenge, as the RFC asks."""
_MAX_ISSUES: Final = 20
"""A request can break the schema a million times over; a client is told about this many."""
_LOCATION_PART: Final = 40
"""A location names fields, but a key the client chose is a field name too: it is cut at this length."""
_TEXT_LIMIT: Final = 200
"""Text that comes from elsewhere (a validator's message, the detail of an ``HTTPException``) is cut here."""
_QUOTES_THE_INPUT: Final[Mapping[str, str]] = {
    "union_tag_invalid": "Input tag does not match any of the expected tags.",
    "uuid_parsing": "Input should be a valid UUID.",
    "bytes_invalid_encoding": "Data is not valid in the encoding the field expects.",
}
"""Pydantic error types whose message repeats part of what the client sent, and what they say instead.

``union_tag_invalid`` quotes the whole tag; ``uuid_parsing`` and ``bytes_invalid_encoding`` quote the first
character or symbol that is wrong and where. No other type in pydantic-core 2.50 (``list_all_errors``) quotes
the input: they are fixed text, a measure of it (a length, a time zone offset, a line and column) or a
parser's own wording, except ``value_error`` and ``assertion_error``, which carry what the validator that
raised them wrote."""
_logger = structlog.stdlib.get_logger(__name__)

type RouteOf = Callable[[Mapping[str, object]], str]
"""The label of the route a request took, from its ASGI scope: what a failure's log line names."""


class Problem(BaseModel):
    """An RFC 9457 problem document: what every failure answers (§9.6). A code may add members of its own."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    type: str = Field(description="urn:ledger:error:<code>")
    title: str
    status: int
    code: str = Field(description="The stable code to match on.")
    detail: str | None = None
    request_id: str | None = Field(default=None, description="The request's id, also its Request-Id header.")


class BadRequestError(InvalidInputError):
    """A request the HTTP layer refuses before reading its body: answered 400, its kind ``INVALID``."""

    code: ClassVar[str] = "bad_request"


def status_of(error: LedgerError) -> HTTPStatus:
    """400 for a ``BadRequestError``, else the status of the error's kind (§9.6)."""
    return HTTPStatus.BAD_REQUEST if isinstance(error, BadRequestError) else _STATUS[error.kind]


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


def _challenge(code: str) -> Mapping[str, str]:
    """The ``WWW-Authenticate`` header of a 401 whose problem has the code ``code``."""
    error = f', error="{_REFUSED_CODE}"' if code == _REFUSED_CODE else ""
    return {"WWW-Authenticate": _CHALLENGE + error}


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


def _logged(detail: object) -> str:
    """The detail of an ``HTTPException`` as the log may carry it: bounded, whatever it is.

    The class says text, FastAPI's takes anything (a mapping, a list of ten thousand, a number).
    """
    return truncate(detail if isinstance(detail, str) else described(detail), _TEXT_LIMIT)


def _report_defect(request: Request, error: Exception, code: str, route_of: RouteOf) -> None:
    """Log where a defect happened, whose request it was and what it was; never the request itself.

    Whose: what the route's dependencies learned of the caller (``request_fields``), never its credentials.
    """
    _logger.error(
        "http.unhandled_error",
        method=request.method,
        route=route_of(request.scope),
        code=code,
        exc_info=error,
        **remembered(request.scope),
    )


def install_problem_handlers(app: FastAPI, *, route_of: RouteOf = declared_route) -> None:
    """Answer every failure of ``app``'s requests as an RFC 9457 document telling a client what it may know.

    ``route_of`` labels the route in the log lines of failures (by default, the template the route declares).
    """

    @app.exception_handler(LedgerError)
    async def ledger_error(request: Request, error: LedgerError) -> JSONResponse:
        status = status_of(error)
        if error.kind is ErrorKind.INTERNAL:
            # A defect of ours: the client gets what any bug gets, the log gets the code and the message.
            _report_defect(request, error, error.code, route_of)
            return _problem(status, INTERNAL_CODE)
        challenge = _challenge(error.code) if error.kind is ErrorKind.UNAUTHENTICATED else None
        return _problem(status, error.code, detail=str(error) or None, headers=challenge)

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
                route=route_of(request.scope),
                status=error.status_code,
                code=code,
                detail=_logged(error.detail),
                **remembered(request.scope),
            )
        if not is_body_allowed_for_status_code(error.status_code):
            return Response(status_code=error.status_code, headers=error.headers)
        return _problem(error.status_code, code, headers=error.headers)

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        """The answer to an exception nothing else handled; ``_Contained`` logs it, once."""
        del request, error
        return _problem(HTTPStatus.INTERNAL_SERVER_ERROR, INTERNAL_CODE)
