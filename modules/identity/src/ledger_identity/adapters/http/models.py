"""The shapes of identity's public API: what clients send (unknown fields refused) and what they receive.

Ids are UUID strings, times ISO 8601 with their offset (UTC), enumerations their lowercase words. A list
answers a ``Page`` of its views (``ledger_platform.http.models.Page``, §9.7).
"""

from datetime import datetime
from typing import ClassVar, Final, Self
from uuid import UUID
lazy from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.text import MAX_LEGAL_NAME
from ledger_kernel.access import Role
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.establishments import Establishment
lazy from ledger_identity.domain.membership import Membership
lazy from ledger_identity.domain.organization import Organization
lazy from ledger_kernel.jurisdiction import Jurisdiction
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


class JurisdictionBody(_Shape):
    """Where a company is: a country and, where law differs inside it, a subdivision."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    country: str = Field(
        min_length=2, max_length=2, description="ISO 3166-1 alpha-2, upper case.", examples=["DO"]
    )
    subdivision: str | None = Field(
        default=None,
        min_length=4,
        max_length=6,
        description="ISO 3166-2, where law differs inside the country.",
        examples=["US-CA"],
    )

    @classmethod
    def of(cls, jurisdiction: Jurisdiction) -> Self:
        """The body of ``jurisdiction``."""
        return cls(country=jurisdiction.country, subdivision=jurisdiction.subdivision)


MAX_TAX_IDS: Final = 5
"""How many tax ids a company lists: a taxpayer has few (an RNC, a cédula, a foreign id)."""


class NewCompany(_Shape):
    """A company to register."""

    jurisdiction: JurisdictionBody
    functional_currency: str = Field(
        min_length=3,
        max_length=3,
        description="ISO 4217: the currency its books are kept in.",
        examples=["DOP"],
    )
    tax_ids: list[TaxIdBody] = Field(
        min_length=1,
        max_length=MAX_TAX_IDS,
        description="One or more, each once; the first is its primary one.",
    )
    legal_name: str = Field(
        min_length=1, max_length=MAX_LEGAL_NAME, description="As registered with the tax authority."
    )
    time_zone: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "An IANA zone: where the days of its main establishment begin and end. "
            "Absent: its regime's; required where no regime is installed."
        ),
        examples=["America/Santo_Domingo"],
    )


class CompanyChanges(_Shape):
    """What can change in a company."""

    legal_name: str = Field(min_length=1, max_length=MAX_LEGAL_NAME)


class CompanyView(_Shape):
    """A company, at its current version (its ``ETag``)."""

    id: UUID
    organization_id: UUID
    jurisdiction: JurisdictionBody
    functional_currency: str
    tax_ids: list[TaxIdBody] = Field(description="The first is its primary one.")
    legal_name: str
    status: CompanyStatus
    fiscal_profile_locked_at: datetime | None = Field(
        description="When its jurisdiction, currency and primary tax id became immutable, if they did."
    )
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, company: Company) -> Self:
        """The view of ``company``."""
        return cls(
            id=company.id,
            organization_id=company.organization,
            jurisdiction=JurisdictionBody.of(company.profile.jurisdiction),
            functional_currency=company.profile.functional_currency.code,
            tax_ids=[TaxIdBody.of(tax_id) for tax_id in company.profile.tax_ids],
            legal_name=company.legal_name,
            status=company.status,
            fiscal_profile_locked_at=company.fiscal_profile_locked_at,
            version=company.version,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )


class RegistrationWarning(_Shape):
    """Something the registration accepted that its sender may want to look at again (§7.3)."""

    code: str = Field(description="What to match on.", examples=["tax_id_checksum_mismatch"])
    field: str = Field(description="Where in the request.", examples=["tax_ids.0"])


class RegisteredCompany(CompanyView):
    """A company just registered, and what the registration accepted with a warning."""

    warnings: list[RegistrationWarning] = Field(
        description="Empty unless something was accepted with a doubt, such as a tax id's check digit."
    )

    @classmethod
    def registered(cls, company: Company, warnings: Sequence[RegistrationWarning]) -> Self:
        """The view of ``company`` with the ``warnings`` its registration raised."""
        view = CompanyView.of(company)
        return cls(
            id=view.id,
            organization_id=view.organization_id,
            jurisdiction=view.jurisdiction,
            functional_currency=view.functional_currency,
            tax_ids=view.tax_ids,
            legal_name=view.legal_name,
            status=view.status,
            fiscal_profile_locked_at=view.fiscal_profile_locked_at,
            version=view.version,
            created_at=view.created_at,
            updated_at=view.updated_at,
            warnings=list(warnings),
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


class NewEstablishment(_Shape):
    """An establishment to open."""

    code: str = Field(
        min_length=1, max_length=10, description="1 to 10 upper-case letters and digits.", examples=["SUC1"]
    )
    name: str = Field(min_length=1, max_length=200)
    time_zone: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="An IANA zone: where its days begin and end. Absent: the company's main establishment's.",
        examples=["America/Santo_Domingo"],
    )


class EstablishmentView(_Shape):
    """An establishment of a company."""

    id: UUID
    company_id: UUID
    code: str
    name: str
    time_zone: str
    created_at: datetime

    @classmethod
    def of(cls, establishment: Establishment) -> Self:
        """The view of ``establishment``."""
        return cls(
            id=establishment.id,
            company_id=establishment.tenant.company,
            code=establishment.code,
            name=establishment.name,
            time_zone=establishment.time_zone,
            created_at=establishment.created_at,
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
