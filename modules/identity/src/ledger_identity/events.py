"""What identity announces (§3.5): the facts other modules build on. Imported by the domain to emit them.

Each is scoped to its company and organization, without a mode (a company and its memberships are shared by
both modes, §7.1); its subject is the company or the membership, and its ``aggregate_version`` the version the
change left. Roles travel as their words: this module sits below the domain and knows no ``Role``.
"""

from dataclasses import dataclass
from typing import ClassVar, final

from ledger_kernel.events import Event
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyRegistered(Event):
    """A company was registered (subject: the company; scope: its tenant, no mode)."""

    TYPE: ClassVar[str] = "identity.company.registered"
    VERSION: ClassVar[int] = 1
    tax_id: TaxId
    legal_name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyUpdated(Event):
    """A company's details changed."""

    TYPE: ClassVar[str] = "identity.company.updated"
    VERSION: ClassVar[int] = 1
    tax_id: TaxId
    legal_name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyDeactivated(Event):
    """A company was deactivated."""

    TYPE: ClassVar[str] = "identity.company.deactivated"
    VERSION: ClassVar[int] = 1


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipGranted(Event):
    """A principal was given a role in a company, or its role changed (subject: the membership)."""

    TYPE: ClassVar[str] = "identity.membership.granted"
    VERSION: ClassVar[int] = 1
    member: SubjectId
    role: str
    previous: str | None
    """The role it held before, if any."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipRevoked(Event):
    """A principal's role in a company was taken away."""

    TYPE: ClassVar[str] = "identity.membership.revoked"
    VERSION: ClassVar[int] = 1
    member: SubjectId
    role: str


type IdentityEvent = (
    CompanyRegistered | CompanyUpdated | CompanyDeactivated | MembershipGranted | MembershipRevoked
)
"""Every event identity publishes (its manifest's ``[[events.publishes]]``)."""
