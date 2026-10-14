"""The shapes of identity's public API: what clients send (unknown fields refused) and what they receive.

Ids are UUID strings, times ISO 8601 with their offset (UTC), enumerations their lowercase words. A list
answers a ``Page`` of its views (``ledger_platform.http.models.Page``, §9.7).
"""

from datetime import datetime
from typing import ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.text import MAX_LEGAL_NAME
from ledger_kernel.access import Role
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
lazy from ledger_identity.domain.branches import Branch, EmissionPoint
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_kernel.tax_ids import TaxId


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class TaxIdBody(_Shape):
    """A tax id qualified by its scheme, which says how the value is read and checked."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    scheme: str = Field(
        description="A scheme a regime declares, <country>.<name>: do.rnc (RNC) or do.cedula (cédula).",
        examples=["do.rnc"],
    )
    value: str = Field(
        min_length=1,
        max_length=MAX_TAX_ID_LENGTH,
        description="As the scheme writes it; separators are ignored (101-85004-3 is read as 101850043).",
        examples=["101-85004-3"],
    )

    @classmethod
    def of(cls, tax_id: TaxId) -> Self:
        """The body of ``tax_id``, as its scheme normalized it."""
        return cls(scheme=tax_id.scheme, value=tax_id.value)


class NewCompany(_Shape):
    """A company to register."""

    tax_id: TaxIdBody
    legal_name: str = Field(
        min_length=1, max_length=MAX_LEGAL_NAME, description="As registered with the tax authority."
    )


class CompanyChanges(_Shape):
    """What can change in a company."""

    legal_name: str = Field(min_length=1, max_length=MAX_LEGAL_NAME)


class CompanyView(_Shape):
    """A company, at its current version (its ``ETag``)."""

    id: UUID
    organization_id: UUID
    tax_id: TaxIdBody
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
            tax_id=TaxIdBody.of(company.tax_id),
            legal_name=company.legal_name,
            status=company.status,
            version=company.version,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )


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
