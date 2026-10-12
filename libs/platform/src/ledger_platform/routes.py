"""What a service's routes declare: who calls, for which company, and how the request applies.

* ``authenticated(verifier)``: a dependency giving the caller (``Principal``) its bearer token proves; 401
  without one that holds. It is also the OpenAPI document's security scheme (HTTP bearer, JWT).
* ``company_in_path(caller, permissions)`` and ``company_in_header(...)``: the company of the request (the
  path's ``company_id``, or the ``Ledger-Company-Id`` header) and what the caller may do there
  (``CompanyAccess``); 404 when it may do nothing there, whichever organization the company is of. The caller
  is proven first: an unauthenticated request never has a company looked up. The header must be sent once
  (else 422 ``invalid_header``), and a route whose path has a ``company_id`` takes its company from there,
  never from the header: one that declares both is a defect (``AmbiguousCompanyError``, at its first request).
* ``page_request``: the ``limit`` and ``cursor`` of a list.
* ``if_match``: the versions an update may apply to; 428 without ``If-Match``. A header sent in several lines
  is read as one list (RFC 9110 section 5.3), at most 1024 characters in all. The route compares the
  version it reads, and sets the next one only where it is still that version (compare and set), else
  ``precondition_failed`` (412). ``etag`` is what it answers in ``ETag``.
* ``idempotency_key``: the ``Idempotency-Key`` every POST requires.
* ``attempt_of``, ``json_response`` and ``answer``: run a POST's work once per key (``Idempotency.once``,
  called before the work's own statements), and answer it.

The dependencies bind what they learn (``organization_id``, ``subject``, ``company_id``) to the log context
for the rest of the request, and unbind it when the request ends. They also leave it in the request
(``request_fields``), for the log line of a failure the chassis reports once they have ended.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, Mapping

import structlog
from fastapi import Depends, Header, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException
lazy from fastapi import Request
lazy from pydantic import BaseModel

from ledger_kernel.errors import InvalidInputError, LedgerError
from ledger_kernel.ids import CompanyId, Tenant, parse_id
from ledger_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest, decode_cursor
from ledger_platform.authentication import MissingTokenError
from ledger_platform.authorization import CompanyAccess, NoCompanyAccessError
from ledger_platform.idempotency import MAX_KEY_LENGTH, Attempt, StoredResponse, canonical_body
from ledger_platform.request_fields import remember
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.authentication import TokenVerifier
lazy from ledger_platform.authorization import PermissionSource
lazy from ledger_platform.idempotency import Outcome

COMPANY_HEADER: Final = "Ledger-Company-Id"
IF_MATCH: Final = "If-Match"
_COMPANY_PARAMETER: Final = "company_id"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
JSON: Final = "application/json"
_ANY: Final = "*"
_MAX_VERSION_DIGITS: Final = 18
"""A version of ours fits a PostgreSQL ``bigint``; a longer tag cannot name one."""
_MAX_CURSOR_LENGTH: Final = 64
_MAX_IF_MATCH_LENGTH: Final = 1024
_NO_HEADERS: Final[Mapping[str, str]] = MappingProxyType({})
_BEARER: Final = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="An OAuth 2.0 access token (JWT) of the organization's identity provider.",
)

type Caller = Callable[[Request, HTTPAuthorizationCredentials | None], AsyncGenerator[Principal]]
"""The dependency ``authenticated`` makes: a route declares ``Annotated[Principal, Depends(caller)]``."""
type PermissionsDependency[**P] = Callable[P, Coroutine[object, object, PermissionSource]]
"""A dependency handing the service's ``PermissionSource``; its parameters (``P``) are dependencies of its
own, such as the service's runtime."""
type InCompany = Callable[[Request, str, Principal, PermissionSource], AsyncGenerator[CompanyAccess]]
"""The dependency ``company_in_path`` or ``company_in_header`` makes: from the request, the company's id, the
caller and the source, the caller's access to that company."""


class InvalidHeaderError(InvalidInputError):
    """A header the request carries in a form no route reads: sent twice, or too long."""

    code: ClassVar[str] = "invalid_header"

    def __init__(self, name: str, requirement: str) -> None:
        super().__init__(f"the header {name} must be {requirement}")


class AmbiguousCompanyError(LedgerError):
    """A route with a ``company_id`` in its path taking its company from the header: a defect of the route.

    Two companies could be named at once, and the header's would win unseen.
    """

    code: ClassVar[str] = "ambiguous_company"

    def __init__(self) -> None:
        super().__init__(f"a route takes its company from its path or from {COMPANY_HEADER}, never both")


@contextmanager
def _bound(request: Request, **fields: str) -> Generator[None]:
    """``fields`` in the log context while the block runs, and in the request for its failure's log line."""
    remember(request.scope, **fields)
    with structlog.contextvars.bound_contextvars(**fields):
        yield


def authenticated(verifier: TokenVerifier) -> Caller:
    """The dependency that gives a route its caller, proven by the request's bearer token."""

    async def caller(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    ) -> AsyncGenerator[Principal]:
        if credentials is None:
            raise MissingTokenError
        principal = await verifier.verify(credentials.credentials)
        with _bound(request, organization_id=str(principal.organization), subject=principal.subject):
            yield principal

    return caller


async def _granted(principal: Principal, company_id: str, source: PermissionSource) -> CompanyAccess:
    """``principal``'s access to the company ``company_id`` names; ``NoCompanyAccessError`` without a role."""
    company = CompanyId(parse_id(company_id))
    permissions = await source.permissions(principal, company)
    if not permissions:
        raise NoCompanyAccessError(company)
    return CompanyAccess(principal, Tenant(principal.organization, company), permissions)


def company_in_path[**P](caller: Caller, permissions: PermissionsDependency[P]) -> InCompany:
    """A route's company from its ``company_id`` path parameter, with the caller's access to it."""

    async def access(
        request: Request,
        company_id: Annotated[str, Path()],
        principal: Annotated[Principal, Depends(caller)],
        source: Annotated[PermissionSource, Depends(permissions)],
    ) -> AsyncGenerator[CompanyAccess]:
        granted = await _granted(principal, company_id, source)
        with _bound(request, company_id=str(granted.tenant.company)):
            yield granted

    return access


def company_in_header[**P](caller: Caller, permissions: PermissionsDependency[P]) -> InCompany:
    """A route's company from the ``Ledger-Company-Id`` header, with the caller's access to it.

    The header must be sent once. A route whose path names a ``company_id`` must not use this: it fails its
    requests with ``AmbiguousCompanyError``. (The header's parameter is not named ``company_id``: FastAPI
    refuses that name beside a path parameter of the same name, but with an ``assert``, which ``python -O``
    strips.)
    """

    async def access(
        request: Request,
        company_header: Annotated[str, Header(alias=COMPANY_HEADER)],
        principal: Annotated[Principal, Depends(caller)],
        source: Annotated[PermissionSource, Depends(permissions)],
    ) -> AsyncGenerator[CompanyAccess]:
        if _COMPANY_PARAMETER in request.path_params:
            raise AmbiguousCompanyError
        if len(request.headers.getlist(COMPANY_HEADER)) > 1:
            raise InvalidHeaderError(COMPANY_HEADER, "sent once")
        granted = await _granted(principal, company_header, source)
        with _bound(request, company_id=str(granted.tenant.company)):
            yield granted

    return access


async def page_request(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many items the page holds.")] = (
        DEFAULT_PAGE_SIZE
    ),
    cursor: Annotated[
        str | None,
        Query(
            max_length=_MAX_CURSOR_LENGTH,
            description="Where the page starts: the next_cursor of the page before.",
        ),
    ] = None,
) -> PageRequest:
    """The page a list request asks for."""
    return PageRequest(size=limit, after=None if cursor is None else decode_cursor(cursor))


@final
@dataclass(frozen=True, slots=True)
class IfMatch:
    """The versions an update may apply to; ``None`` for any (``If-Match: *``)."""

    versions: frozenset[int] | None

    def admits(self, version: int) -> bool:
        """Whether the current ``version`` is one the client allowed the update to apply to."""
        return self.versions is None or version in self.versions


def etag(version: int) -> str:
    """The strong entity tag of ``version``."""
    return f'"{version}"'


def _version_of(tag: str) -> int | None:
    """The version a strong tag of ours names; ``None`` for anything else (a weak tag never matches)."""
    digits = tag.removeprefix('"').removesuffix('"')
    if (
        tag != f'"{digits}"'
        or not 0 < len(digits) <= _MAX_VERSION_DIGITS
        or not digits.isascii()
        or not digits.isdigit()
        or (len(digits) > 1 and digits.startswith("0"))  # one spelling per version: the one ``etag`` gives
    ):
        return None
    return int(digits)


def parse_if_match(value: str) -> IfMatch:
    """The ``If-Match`` header read with strong comparison (RFC 9110): ``*``, one tag or a list of them."""
    text = value.strip()
    if text == _ANY:
        return IfMatch(None)
    versions = (_version_of(part.strip()) for part in text.split(","))
    return IfMatch(frozenset(version for version in versions if version is not None))


async def if_match(
    request: Request,
    value: Annotated[str | None, Header(alias=IF_MATCH, max_length=_MAX_IF_MATCH_LENGTH)] = None,
) -> IfMatch:
    """The ``If-Match`` an update requires: 428 when the request has none.

    Every line of the header counts, read as one list (RFC 9110 section 5.3); ``value``, the line FastAPI
    reads, says whether there is any.
    """
    if value is None:
        raise HTTPException(HTTPStatus.PRECONDITION_REQUIRED)
    joined = ", ".join(request.headers.getlist(IF_MATCH))
    if len(joined) > _MAX_IF_MATCH_LENGTH:
        raise InvalidHeaderError(IF_MATCH, f"at most {_MAX_IF_MATCH_LENGTH} characters in all")
    return parse_if_match(joined)


def precondition_failed() -> HTTPException:
    """What an update answers when ``If-Match`` names no current version: 412."""
    return HTTPException(HTTPStatus.PRECONDITION_FAILED)


async def idempotency_key(
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=MAX_KEY_LENGTH,
            pattern=r"^[!-~]+$",
            description="Makes a retry safe: the same key and body change state once and answer the same.",
        ),
    ],
) -> str:
    """The ``Idempotency-Key`` every POST requires (§10)."""
    return key


def attempt_of(request: Request, principal: Principal, key: str, body: BaseModel | None = None) -> Attempt:
    """``principal``'s use of ``key`` for ``request``: the body counts as its validated, canonical JSON."""
    return Attempt.of(principal, key, method=request.method, path=request.url.path, body=canonical_body(body))


def json_response(status: int, body: BaseModel, headers: Mapping[str, str] = _NO_HEADERS) -> StoredResponse:
    """A JSON response as it is stored for replays."""
    return StoredResponse(status, body.model_dump_json().encode(), {"Content-Type": JSON, **headers})


def answer(outcome: Outcome) -> Response:
    """The response of an idempotent request: byte for byte the stored one, marked when it is a replay."""
    headers = dict(outcome.response.headers)
    if outcome.replayed:
        headers[REPLAYED_HEADER] = "true"
    return Response(content=outcome.response.body, status_code=outcome.response.status, headers=headers)
