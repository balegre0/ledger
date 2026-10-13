"""The shapes of identity's public API: what clients send (unknown fields refused) and what they receive.

Ids are UUID strings, times ISO 8601 with their offset (UTC), enumerations their lowercase words.
"""

from datetime import datetime
from typing import ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.roles import Role
from ledger_kernel.countries.do import TaxIdKind
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Problem(BaseModel):
    """An RFC 9457 problem document: what every failure answers."""

    type: str = Field(description="urn:ledger:error:<code>")
    title: str
    status: int
    code: str = Field(description="The stable code to match on.")
    detail: str | None = None


class NewCompany(_Shape):
    """A company to register."""

    tax_id: str = Field(
        min_length=1,
        max_length=20,
        description="RNC (9 digits) or cedula (11 digits); hyphens and spaces are ignored.",
        examples=["101-85004-3"],
    )
    legal_name: str = Field(
        min_length=1, max_length=300, description="As registered with DGII; up to 150 characters."
    )


class CompanyChanges(_Shape):
    """What can change in a company."""

    legal_name: str = Field(min_length=1, max_length=300)


class CompanyView(_Shape):
    """A company, at its current version (its ``ETag``)."""

    id: UUID
    organization_id: UUID
    tax_id: str
    tax_id_kind: TaxIdKind
    legal_name: str
    status: CompanyStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, company: Company) -> Self:
        """The view of ``company``."""
        return cls(
            id=company.id,
            organization_id=company.organization,
            tax_id=str(company.tax_id),
            tax_id_kind=company.tax_id.kind,
            legal_name=company.legal_name,
            status=company.status,
            version=company.version,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )


class CompanyPage(_Shape):
    """Companies in id order, and the cursor of the next page (``null`` on the last)."""

    items: list[CompanyView]
    next_cursor: str | None


class OrganizationView(_Shape):
    """The caller's organization."""

    id: UUID
    provider_id: str = Field(description="The identity provider's (Zitadel's) id of the organization.")
    created_at: datetime

    @classmethod
    def of(cls, organization: Organization) -> Self:
        """The view of ``organization``."""
        return cls(
            id=organization.id, provider_id=organization.provider_id, created_at=organization.created_at
        )


class NewBranch(_Shape):
    """A branch to open."""

    code: str = Field(
        min_length=1, max_length=10, description="1 to 10 upper-case letters and digits.", examples=["SUC1"]
    )
    name: str = Field(min_length=1, max_length=200)


class BranchView(_Shape):
    """A branch of a company."""

    id: UUID
    company_id: UUID
    code: str
    name: str
    created_at: datetime

    @classmethod
    def of(cls, branch: Branch) -> Self:
        """The view of ``branch``."""
        return cls(
            id=branch.id,
            company_id=branch.tenant.company,
            code=branch.code,
            name=branch.name,
            created_at=branch.created_at,
        )


class BranchPage(_Shape):
    """Branches in id order, and the cursor of the next page."""

    items: list[BranchView]
    next_cursor: str | None


class NewEmissionPoint(_Shape):
    """An emission point to open."""

    code: str = Field(
        min_length=1, max_length=10, description="1 to 10 upper-case letters and digits.", examples=["001"]
    )
    name: str = Field(min_length=1, max_length=200)


class EmissionPointView(_Shape):
    """An emission point of a branch."""

    id: UUID
    company_id: UUID
    branch_id: UUID
    code: str
    name: str
    created_at: datetime

    @classmethod
    def of(cls, point: EmissionPoint) -> Self:
        """The view of ``point``."""
        return cls(
            id=point.id,
            company_id=point.tenant.company,
            branch_id=point.branch,
            code=point.code,
            name=point.name,
            created_at=point.created_at,
        )


class EmissionPointPage(_Shape):
    """Emission points in id order, and the cursor of the next page."""

    items: list[EmissionPointView]
    next_cursor: str | None


class RoleAssignment(_Shape):
    """The role to hold in a company."""

    role: Role


class MembershipView(_Shape):
    """A principal's role in a company: an active membership, whose last change is the grant of its role."""

    id: UUID
    company_id: UUID
    subject: str = Field(description="The identity provider's id of the user or machine.")
    role: Role
    version: int = Field(description="One more with every change; a role granted again continues it.")
    granted_at: datetime
    granted_by: str

    @classmethod
    def of(cls, membership: Membership) -> Self:
        """The view of ``membership``, which is active."""
        return cls(
            id=membership.id,
            company_id=membership.tenant.company,
            subject=membership.subject,
            role=membership.role,
            version=membership.version,
            granted_at=membership.changed_at,
            granted_by=membership.changed_by,
        )


class MembershipPage(_Shape):
    """Memberships in id order, and the cursor of the next page."""

    items: list[MembershipView]
    next_cursor: str | None
