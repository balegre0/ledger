"""What the use cases need of the world: identity's state, read and changed inside one unit of work.

A unit is one transaction scoped to an organization or one of its companies: row-level security limits what
its repositories see to that scope, and every repository is bound to the unit's organization and names it,
and the company where a row is a company's, in every statement (tenancy is checked twice, §2). The methods
are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Literal, Protocol, final
lazy from collections.abc import Mapping
lazy from datetime import datetime
lazy from uuid import UUID

lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.establishments import Establishment
lazy from ledger_identity.domain.ids import EstablishmentId
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_identity.domain.text import Code
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.pagination import Page, PageRequest


class Organizations(Protocol, metaclass=ABCMeta):
    """The organizations identity has seen."""

    @abstractmethod
    async def add(self, organization: Organization) -> None:
        """Keep ``organization``; one already kept stays as it was."""

    @abstractmethod
    async def get(self, organization: OrganizationId) -> Organization | None:
        """The organization, or ``None`` before its first company."""


class Companies(Protocol, metaclass=ABCMeta):
    """The companies of the unit's organization."""

    @abstractmethod
    async def add(self, company: Company) -> bool:
        """Keep a new company; false when the organization has a company with its tax id."""

    @abstractmethod
    async def get(self, company: CompanyId) -> Company | None:
        """The company, or ``None``."""

    @abstractmethod
    async def locked(self, company: CompanyId) -> Company | None:
        """The company, locked until the unit ends.

        Whatever is decided on it (its memberships, its being active, a new establishment) waits
        for the other units that lock it.
        """

    @abstractmethod
    async def update(self, company: Company, *, version: int) -> bool:
        """Replace the company kept at ``version`` by ``company``; false when it is not at that version."""

    @abstractmethod
    async def of_member(self, subject: SubjectId, request: PageRequest) -> Page[Company]:
        """The companies of the unit's organization in which ``subject`` holds an active role, in id order."""


class Establishments(Protocol, metaclass=ABCMeta):
    """The establishments of the unit's company."""

    @abstractmethod
    async def add(self, establishment: Establishment) -> bool:
        """Keep a new establishment; false when its company has one with its code."""

    @abstractmethod
    async def get(self, company: CompanyId, establishment: EstablishmentId) -> Establishment | None:
        """The establishment of ``company``, or ``None``."""

    @abstractmethod
    async def by_code(self, company: CompanyId, code: Code) -> Establishment | None:
        """The establishment of ``company`` known by ``code``, or ``None``."""

    @abstractmethod
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Establishment]:
        """The establishments of ``company``, in id order."""


class Memberships(Protocol, metaclass=ABCMeta):
    """Who holds, or held, which role in the companies of the unit's organization."""

    @abstractmethod
    async def get(self, company: CompanyId, subject: SubjectId) -> Membership | None:
        """``subject``'s membership in ``company``, active or revoked; ``None`` if it never held a role."""

    @abstractmethod
    async def put(self, membership: Membership) -> None:
        """Keep ``membership``, in place of the subject's previous one in the company (a revocation too)."""

    @abstractmethod
    async def owners(self, company: CompanyId) -> int:
        """How many active owners ``company`` has."""

    @abstractmethod
    async def page(self, company: CompanyId, request: PageRequest) -> Page[Membership]:
        """The active memberships of ``company``, in id order."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces once it commits."""

    @abstractmethod
    def publish(self, event: IdentityEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox, written with the unit)."""


type AuditAction = Literal[
    "company.register",
    "company.rename",
    "company.deactivate",
    "membership.grant",
    "membership.revoke",
    "establishment.open",
]
"""What a use case records it did (§10: identity's changes are audited); the part before the dot names the
kind of subject."""


class Audit(Protocol, metaclass=ABCMeta):
    """Who changed what, recorded with the change (``audit_events``, §6.4): it commits with the unit, or not.

    The actor is the call the unit runs in, not the use case's to name.
    """

    @abstractmethod
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        """Record that ``action`` changed ``subject`` from ``before`` to ``after`` (``None``: not there).

        Only hashes of the two are kept; ``detail`` holds ids and codes only.
        """


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityUnit:
    """One unit of work: identity's state as one transaction sees it, and what it announces."""

    organizations: Organizations
    companies: Companies
    establishments: Establishments
    memberships: Memberships
    events: Events
    audit: Audit
    now: datetime
    """The unit's time: everything it records and announces happened then, as one transaction does."""
