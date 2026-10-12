"""Authorization: what a principal may do in a company, looked up per request and checked by name.

A ``PermissionSource`` answers with the permissions a principal holds in a company of its organization. The
identity service reads its memberships; every other service asks identity over gRPC (``identity_client``),
through a ``CachedPermissions``. An empty answer means no role there, and the company is then answered as not
found (``NoCompanyAccessError``): a caller learns nothing of companies it has no part in, its own
organization's or another's. A role without the permission an operation needs is a ``PermissionDeniedError``.
"""

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import ClassVar, Final, Protocol, final, override

from ledger_kernel.errors import ForbiddenError, NotFoundError
from ledger_platform.options import InvalidOptionError, require_at_least, require_seconds
lazy from ledger_kernel.access import Permission, Principal, SubjectId
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_kernel.ids import CompanyId, OrganizationId, Tenant

DEFAULT_TTL: Final = 30.0
"""Seconds an answer is kept: how long a revoked role may still work in another service, at most."""
DEFAULT_CAPACITY: Final = 10_000
"""How many answers are kept, the most recently used: a bound on the memory a flood of callers can take."""


class PermissionDeniedError(ForbiddenError):
    """The caller holds a role in the company, but not one that allows this."""

    code: ClassVar[str] = "permission_denied"

    permission: Permission

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission.value}")
        self.permission = permission


class NoCompanyAccessError(NotFoundError):
    """The caller holds no role in the company: for it, the company does not exist."""

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: CompanyId) -> None:
        super().__init__(f"company {company} was not found")


class InvalidAuthorizationOptionError(InvalidOptionError):
    """An authorization option that cannot work."""

    code: ClassVar[str] = "invalid_authorization_option"
    label: ClassVar[str] = "authorization option"


class PermissionSource(Protocol, metaclass=ABCMeta):
    """Where the permissions of a principal in a company come from.

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        """What ``principal`` may do in ``company`` of its organization; empty without a role there."""


@final
@dataclass(frozen=True, slots=True)
class CompanyAccess:
    """A request's caller, the company it acts on (with its organization) and what it may do there.

    ``tenant`` is the scope of a company's transaction; ``principal.organization`` that of an organization's.
    """

    principal: Principal
    tenant: Tenant
    permissions: frozenset[Permission]

    def allows(self, permission: Permission) -> bool:
        """Whether the caller holds ``permission`` in the company."""
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Refuse with ``PermissionDeniedError`` unless the caller holds ``permission`` in the company."""
        if permission not in self.permissions:
            raise PermissionDeniedError(permission)


type _Key = tuple[OrganizationId, CompanyId, SubjectId]
"""Whose answer: the organization is part of it, so a namesake in another organization never shares one."""


@final
class CachedPermissions(PermissionSource):
    """A ``PermissionSource`` that keeps answers ``ttl`` seconds, the ``capacity`` most recently used of them.

    ``forget`` drops an answer at once, when identity announces a membership change. One fetched before a
    ``forget`` and arriving after it is handed to its caller but not kept: it may be the old one. Two requests
    that miss together ask the source twice; both answers are right.
    """

    __slots__ = ("_answers", "_capacity", "_clock", "_epoch", "_source", "_ttl")

    def __init__(
        self,
        source: PermissionSource,
        *,
        ttl: float = DEFAULT_TTL,
        capacity: int = DEFAULT_CAPACITY,
        clock: MonotonicClock = monotonic,
    ) -> None:
        require_seconds(InvalidAuthorizationOptionError, "ttl", ttl)
        require_at_least(InvalidAuthorizationOptionError, "capacity", capacity)
        self._source = source
        self._ttl = ttl
        self._capacity = capacity
        self._clock = clock
        self._answers: OrderedDict[_Key, tuple[float, frozenset[Permission]]] = OrderedDict()
        self._epoch = 0
        """How many times something was forgotten: an answer fetched across a change is not kept."""

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        key = (principal.organization, company, principal.subject)
        cached = self._answers.get(key)
        if cached is not None and self._clock() - cached[0] < self._ttl:
            self._answers.move_to_end(key)
            return cached[1]
        epoch = self._epoch
        answer = await self._source.permissions(principal, company)
        if epoch == self._epoch:  # nothing was forgotten while the source answered
            self._answers[key] = (self._clock(), answer)
            self._answers.move_to_end(key)
            while len(self._answers) > self._capacity:
                self._answers.popitem(last=False)
        return answer

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``, and keep no answer fetched before this."""
        self._epoch += 1
        self._answers.pop((organization, company, subject), None)
