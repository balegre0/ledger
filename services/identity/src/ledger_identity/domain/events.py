"""What identity announces (§4.1): the facts other services build on, as the domain states them."""

from dataclasses import dataclass
from typing import final

lazy from ledger_identity.domain.roles import Role
lazy from ledger_identity.domain.text import LegalName
lazy from ledger_kernel.access import SubjectId
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyCreated:
    """A company was registered."""

    tenant: Tenant
    tax_id: TaxId
    legal_name: LegalName
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyUpdated:
    """A company's details changed."""

    tenant: Tenant
    tax_id: TaxId
    legal_name: LegalName
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyDeactivated:
    """A company was deactivated."""

    tenant: Tenant
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipGranted:
    """A principal was given a role in a company, or its role changed (``previous`` is the role it held)."""

    tenant: Tenant
    subject: SubjectId
    role: Role
    previous: Role | None
    version: int


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipRevoked:
    """A principal's role in a company was taken away."""

    tenant: Tenant
    subject: SubjectId
    role: Role
    version: int


type IdentityEvent = (
    CompanyCreated | CompanyUpdated | CompanyDeactivated | MembershipGranted | MembershipRevoked
)
