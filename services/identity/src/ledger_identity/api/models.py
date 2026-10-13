"""The shapes of identity's public API: what clients send (unknown fields refused) and what they receive.

Ids are UUID strings, times ISO 8601 with their offset (UTC), enumerations their lowercase words.
"""

from datetime import datetime
from typing import ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.domain.company import CompanyStatus
from ledger_kernel.countries.do import TaxIdKind
lazy from ledger_identity.domain.company import Company
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
