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
"""Seconds an answer is kept from when it was asked for: how long a revoked role may still work in another
service, at most."""
DEFAULT_CAPACITY: Final = 10_000
"""How many answers that grant something are kept, the most recently used."""
DEFAULT_EMPTY_CAPACITY: Final = 1_000
"""How many answers without a role are kept, apart: a caller asking about random company ids fills these
only, and never pushes out the answers of others."""


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
class _Kept:
    """Answers by whose they are, each with when it was asked for: the ``capacity`` most recently used."""

    __slots__ = ("_answers", "_capacity")

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._answers: OrderedDict[_Key, tuple[float, frozenset[Permission]]] = OrderedDict()

    def fresh(self, key: _Key, since: float) -> frozenset[Permission] | None:
        """The answer kept for ``key`` if it was asked for after ``since``, now the most recently used."""
        kept = self._answers.get(key)
        if kept is None or kept[0] <= since:
            return None
        self._answers.move_to_end(key)
        return kept[1]

    def keep(self, key: _Key, asked: float, answer: frozenset[Permission]) -> None:
        """Keep ``answer``, asked for at ``asked``; the least recently used beyond the capacity go."""
        self._answers[key] = (asked, answer)
        self._answers.move_to_end(key)
        while len(self._answers) > self._capacity:
            self._answers.popitem(last=False)

    def drop(self, key: _Key) -> None:
        """Drop the answer kept for ``key``, if any."""
        self._answers.pop(key, None)

    def drop_company(self, organization: OrganizationId, company: CompanyId) -> None:
        """Drop every answer about ``company`` of ``organization``, whoever it was for."""
        for key in [key for key in self._answers if key[:2] == (organization, company)]:
            del self._answers[key]

    def clear(self) -> None:
        """Drop every answer."""
        self._answers.clear()


@final
class CachedPermissions(PermissionSource):
    """A ``PermissionSource`` that keeps answers ``ttl`` seconds from when they were asked for.

    The ``capacity`` most recently used answers that grant something are kept, and apart from them the
    ``empty_capacity`` most recently used answers without a role: a caller trying random company ids churns
    the latter only. ``forget`` drops an answer at once, when identity announces a membership change;
    ``forget_company`` every answer about a company, when identity announces its deactivation; and
    ``forget_all`` every answer, when such announcements may have been missed (``identity_client``). One
    fetched before any of them and arriving after it is handed to its caller but not kept: it may be the old
    one.
    Two requests that miss together ask the source twice; both answers are right.
    """

    __slots__ = ("_clock", "_empty", "_epoch", "_granting", "_source", "_ttl")

    def __init__(
        self,
        source: PermissionSource,
        *,
        ttl: float = DEFAULT_TTL,
        capacity: int = DEFAULT_CAPACITY,
        empty_capacity: int = DEFAULT_EMPTY_CAPACITY,
        clock: MonotonicClock = monotonic,
    ) -> None:
        require_seconds(InvalidAuthorizationOptionError, "ttl", ttl)
        require_at_least(InvalidAuthorizationOptionError, "capacity", capacity)
        require_at_least(InvalidAuthorizationOptionError, "empty_capacity", empty_capacity)
        self._source = source
        self._ttl = ttl
        self._clock = clock
        self._granting = _Kept(capacity)
        self._empty = _Kept(empty_capacity)
        """A key is kept in one of the two at most: an answer kept in one drops the other's."""
        self._epoch = 0
        """How many times something was forgotten: an answer fetched across a change is not kept."""

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        key = (principal.organization, company, principal.subject)
        asked = self._clock()
        for kept in (self._granting, self._empty):
            answer = kept.fresh(key, asked - self._ttl)
            if answer is not None:
                return answer
        epoch = self._epoch
        answer = await self._source.permissions(principal, company)
        if epoch == self._epoch:  # nothing was forgotten while the source answered
            kept, other = (self._granting, self._empty) if answer else (self._empty, self._granting)
            other.drop(key)
            kept.keep(key, asked, answer)  # from when it was asked for: the ttl bounds its age, wait included
        return answer

    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        """Drop what is kept about ``subject`` in ``company``, and keep no answer fetched before this."""
        self._epoch += 1
        key = (organization, company, subject)
        self._granting.drop(key)
        self._empty.drop(key)

    def forget_company(self, organization: OrganizationId, company: CompanyId) -> None:
        """Drop every answer about ``company``, and keep none fetched before this: what it grants changed.

        A scan of the kept answers (at most ``capacity`` plus ``empty_capacity``): deactivations are rare.
        """
        self._epoch += 1
        self._granting.drop_company(organization, company)
        self._empty.drop_company(organization, company)

    def forget_all(self) -> None:
        """Drop every answer, and keep none fetched before this: changes may have gone unannounced."""
        self._epoch += 1
        self._granting.clear()
        self._empty.clear()
