"""What a module's routes declare: who calls, for which company, and how the request applies.

* ``authenticated(verifier)``: a dependency giving the caller (``Principal``) its bearer token proves; 401
  without one that holds. It is also the OpenAPI document's security scheme (HTTP bearer, JWT).
* ``company_in_path(caller, permissions)`` and ``company_in_header(...)``: the company of the request (the
  path's ``company_id``, or the ``Ledger-Company`` header, §9.7) and what the caller may do there
  (``CompanyAccess``); 404 when it may do nothing there, whichever organization the company is of. The caller
  is proven first: an unauthenticated request never has a company looked up. The header must be sent once
  (else 422 ``invalid_header``), and a route whose path has a ``company_id`` takes its company from there,
  never from the header: one that declares both is a defect (``AmbiguousCompanyError``, at its first request).
* ``allowed(in_company, permission)``, ``holding(caller, role)`` and ``in_organization(caller)``: what a route
  requires, declared as its first dependency: a permission in the request's company (403 without it, after
  the 404 of no role there), a role the identity provider granted in the organization (403), or no more than
  being a principal of the organization, for a route about the organization as a whole. ``unguarded_routes``
  names every route of an app that needs no bearer token or declares none of them: a module's test asserts it
  finds none under ``/v1``.
* ``if_match``: the versions an update may apply to; 428 without ``If-Match``. A header sent in several lines
  is read as one list (RFC 9110 section 5.3), at most 1024 characters in all. The route compares the
  version it reads, and sets the next one only where it is still that version (compare and set), else
  answers 412 (``precondition_checked``). ``etag`` is what it answers in ``ETag``. Its route documents the
  header with ``openapi_extra=if_match_required()``.

The dependencies bind what they learn (``organization_id``, ``subject``, ``company_id``) to the log context
for the rest of the request, and unbind it when the request ends. They also leave it in the request
(``request_fields``), for the log line of a failure the chassis reports once they have ended.
``authenticated`` also makes the caller the actor of what the request audits (``acting_as``, Decision 10):
its subject, in the call its request id names, or in a call of its own (a UUIDv7) where it is not traced.
"""

from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, Protocol, final
from weakref import WeakSet
lazy from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, Iterator, Sequence

import structlog
from fastapi import Depends, Header, Path
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException
lazy from fastapi import Request
lazy from fastapi.dependencies.models import Dependant
lazy from starlette.routing import BaseRoute

from ledger_kernel.access import CompanyNotFoundError
from ledger_kernel.errors import ForbiddenError, InvalidInputError, LedgerError
from ledger_kernel.ids import COMPANY_PREFIX, CompanyId, InvalidPublicIdError, new_id, parse_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.access import CompanyAccess
from ledger_platform.auth.tokens import MissingTokenError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.http.problems import current_request_id
from ledger_platform.http.rate import RateLimitedError
from ledger_platform.http.request_fields import remember
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_platform.auth.access import PermissionSource
lazy from ledger_platform.http.rate import RateLimiter

COMPANY_HEADER: Final = "Ledger-Company"
IF_MATCH: Final = "If-Match"
_COMPANY_PARAMETER: Final = "company_id"
_ANY: Final = "*"
_MAX_VERSION_DIGITS: Final = 18
"""A version of ours fits a PostgreSQL ``bigint``; a longer tag cannot name one."""
_MAX_IF_MATCH_LENGTH: Final = 1024
_BEARER: Final = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="An OAuth 2.0 access token (JWT) of the organization's identity provider.",
)


class Verifier(Protocol, metaclass=ABCMeta):
    """What proves a bearer token: ``TokenVerifier``, or what an app without token settings has instead."""

    @abstractmethod
    async def verify(self, token: str) -> Principal:
        """The principal ``token`` proves, else an ``UnauthenticatedError``."""


type Caller = Callable[[Request, HTTPAuthorizationCredentials | None], AsyncGenerator[Principal]]
"""The dependency ``authenticated`` makes: a route declares ``Annotated[Principal, Depends(caller)]``."""
type PermissionsDependency[**P] = Callable[P, Coroutine[object, object, PermissionSource]]
"""A dependency handing the module's ``PermissionSource``; its parameters (``P``) are dependencies of its
own, such as the module's context."""
type InCompany = Callable[[Request, str, Principal, PermissionSource], AsyncGenerator[CompanyAccess]]
"""The dependency ``company_in_path`` or ``company_in_header`` makes: from the request, the company's id, the
caller and the source, the caller's access to that company."""
type Allowed = Callable[[CompanyAccess], Coroutine[object, object, CompanyAccess]]
"""The dependency ``allowed`` makes: the caller's access to the request's company, its permission checked."""
type InOrganization = Callable[[Principal], Coroutine[object, object, Principal]]
"""The dependency ``holding`` or ``in_organization`` makes: the caller, acting in its whole organization."""

_GUARDS: Final[WeakSet[object]] = WeakSet()
"""Every dependency ``allowed``, ``holding`` and ``in_organization`` made: how ``unguarded_routes`` tells a
route that declares what it requires."""
_NO_REQUIREMENT: Final = "declares no requirement (allowed, holding or in_organization)"


class InvalidHeaderError(InvalidInputError):
    """A header the request carries in a form no route reads: sent twice, or too long."""

    code: ClassVar[str] = "invalid_header"

    def __init__(self, name: str, requirement: str) -> None:
        super().__init__(f"the header {name} must be {requirement}")


class MissingRoleError(ForbiddenError):
    """The caller is a principal of the organization, but the identity provider did not grant it the role."""

    def __init__(self, role: str) -> None:
        super().__init__(f"this needs the role {role} in the organization")


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


def authenticated(verifier: Verifier, limiter: RateLimiter | None = None) -> Caller:
    """The dependency that gives a route its caller, proven by the request's bearer token.

    With a ``limiter``, a caller over its rate is refused (429) once it is proven: nobody is counted, and so
    nobody can be limited, by presenting a token that does not verify.
    """

    async def caller(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    ) -> AsyncGenerator[Principal]:
        if credentials is None:
            raise MissingTokenError
        principal = await verifier.verify(credentials.credentials)
        if (
            limiter is not None
            and (wait := limiter.wait(f"{principal.organization}:{principal.subject}")) > 0
        ):
            raise RateLimitedError(wait)
        call = current_request_id() or str(new_id())
        with (
            _bound(request, organization_id=str(principal.organization), subject=principal.subject),
            acting_as(Actor(principal.subject, call)),
        ):
            yield principal

    return caller


async def _granted(principal: Principal, company_id: str, source: PermissionSource) -> CompanyAccess:
    """``principal``'s access to the company ``company_id`` names; ``CompanyNotFoundError`` without a role.

    A company is named by its public id (``cmp_…``, §9.4): any other text, an internal UUID or another type's
    id included, names no company, and is not repeated.
    """
    try:
        company = CompanyId(parse_id(COMPANY_PREFIX, company_id))
    except InvalidPublicIdError:
        raise CompanyNotFoundError from None
    permissions = await source.permissions(principal, company)
    if not permissions:
        raise CompanyNotFoundError(company)
    return CompanyAccess(principal, Tenant(principal.organization, company, None), permissions)


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
    """A route's company from the ``Ledger-Company`` header, with the caller's access to it.

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


def allowed(in_company: InCompany, permission: Permission) -> Allowed:
    """The dependency giving the caller's access to the request's company, if it holds ``permission`` there.

    Declared before any other dependency of the route: a caller without the permission is refused (403) before
    the request's preconditions (``If-Match``, ``Idempotency-Key``) are read. FastAPI reads and parses a body
    before it runs any dependency, so what keeps an unauthorized caller from making the app hold a huge one is
    the body limit (``http.limits``), not this order.
    """

    async def checked(access: Annotated[CompanyAccess, Depends(in_company)]) -> CompanyAccess:
        access.require(permission)
        return access

    _GUARDS.add(checked)
    return checked


def holding(caller: Caller, role: str) -> InOrganization:
    """The dependency giving the caller, if the identity provider granted it ``role`` in its organization.

    For what the organization does as a whole (registering a company); ``MissingRoleError`` (403) otherwise.
    """

    async def checked(principal: Annotated[Principal, Depends(caller)]) -> Principal:
        if role not in principal.roles:
            raise MissingRoleError(role)
        return principal

    _GUARDS.add(checked)
    return checked


def in_organization(caller: Caller) -> InOrganization:
    """The dependency giving the caller, for a route about its own organization that any principal may use.

    The caller's own organization is the route's scope, and what the route answers is filtered by the caller's
    roles (the companies it holds a role in), so it requires nothing beyond the token: this says so.
    """

    async def member(principal: Annotated[Principal, Depends(caller)]) -> Principal:
        return principal

    _GUARDS.add(member)
    return member


def _calls(dependant: Dependant) -> Iterator[object]:
    """What ``dependant`` and every dependency below it call."""
    yield dependant.call
    for dependency in dependant.dependencies:
        yield from _calls(dependency)


def _declared(call: object) -> bool:
    return callable(call) and call in _GUARDS


def unguarded_routes(routes: Sequence[BaseRoute], *, prefix: str = "/v1") -> tuple[str, ...]:
    """Every route under ``prefix`` that needs no bearer token, or declares no requirement, and why.

    ``routes`` are an app's (``app.routes``), its included routers' too. A route needs a token when it
    depends, directly or through another dependency, on ``authenticated``'s bearer scheme; it declares a
    requirement when one of its dependencies was made by ``allowed``, ``holding`` or ``in_organization``. Both
    are read from the route's own dependencies: a dependency an ``APIRouter`` adds to all its routes is not
    seen. Empty when every route is guarded: an app's test asserts so.
    """
    found: list[str] = []
    for context in iter_route_contexts(routes):
        route, path = context.original_route, context.path
        if not isinstance(route, APIRoute) or path is None or not path.startswith(prefix):
            continue
        calls = list(_calls(route.dependant))
        missing = [
            *(() if _BEARER in calls else ("needs no bearer token",)),
            *(() if any(_declared(call) for call in calls) else (_NO_REQUIREMENT,)),
        ]
        if missing:
            why = "; ".join(missing)
            found.extend(f"{method} {path}: {why}" for method in sorted(route.methods or ()))
    return tuple(found)


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
    value: Annotated[
        str | None, Header(alias=IF_MATCH, max_length=_MAX_IF_MATCH_LENGTH, include_in_schema=False)
    ] = None,
) -> IfMatch:
    """The ``If-Match`` an update requires: 428 when the request has none.

    Every line of the header counts, read as one list (RFC 9110 section 5.3); ``value``, the line FastAPI
    reads, says whether there is any. It is read as optional, so that its absence is a 428 rather than a 422,
    and is kept out of the document, which would call it optional: ``if_match_required`` documents it.
    """
    if value is None:
        raise HTTPException(HTTPStatus.PRECONDITION_REQUIRED)
    joined = ", ".join(request.headers.getlist(IF_MATCH))
    if len(joined) > _MAX_IF_MATCH_LENGTH:
        raise InvalidHeaderError(IF_MATCH, f"at most {_MAX_IF_MATCH_LENGTH} characters in all")
    return parse_if_match(joined)


def if_match_required() -> dict[str, object]:
    """The ``openapi_extra`` of a route that depends on ``if_match``: ``If-Match``, a required header.

    A new mapping each time: FastAPI merges it into the route's operation.
    """
    parameter: dict[str, object] = {
        "name": IF_MATCH,
        "in": "header",
        "required": True,
        "description": (
            "The versions the change may apply to: strong entity tags as ETag gives them, or *. "
            "Without it the answer is 428; when no version it names is current, 412."
        ),
        "schema": {"type": "string", "maxLength": _MAX_IF_MATCH_LENGTH},
    }
    return {"parameters": [parameter]}
