"""A company: one taxpayer of an organization, and the unit of tenancy (§7).

Its tax id never changes (another tax id is another company). Every change makes a new version, which is the
company's ``ETag``. A deactivated company changes no more; its data stays readable.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.errors import CompanyInactiveError, InvalidStateError
from ledger_identity.events import CompanyDeactivated, CompanyRegistered, CompanyUpdated
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.tenancy import Tenant
lazy from ledger_identity.domain.text import LegalName
lazy from ledger_kernel.countries.do import TaxId
lazy from ledger_kernel.ids import OrganizationId


class CompanyStatus(StrEnum):
    """Whether a company can still change."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Company:
    """A company and its version: each method returns the company changed and the event that says so."""

    id: CompanyId
    organization: OrganizationId
    tax_id: TaxId
    legal_name: LegalName
    status: CompanyStatus
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidStateError(field="company.version", requirement="at least 1")

    @property
    def tenant(self) -> Tenant:
        """The company with its organization: what its rows and its events are scoped to."""
        return Tenant(self.organization, self.id, None)

    @classmethod
    def register(
        cls, *, organization: OrganizationId, tax_id: TaxId, legal_name: LegalName, now: datetime
    ) -> tuple[Company, CompanyRegistered]:
        """A new company of ``organization``, active, at version 1."""
        company = cls(
            id=CompanyId(new_id()),
            organization=organization,
            tax_id=tax_id,
            legal_name=legal_name,
            status=CompanyStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
        )
        registered = CompanyRegistered(
            scope=company.tenant,
            subject=company.id,
            aggregate_version=1,
            tax_id=tax_id,
            legal_name=legal_name,
        )
        return company, registered

    def require_active(self) -> None:
        """Refuse any change of a deactivated company."""
        if self.status is CompanyStatus.INACTIVE:
            raise CompanyInactiveError(self.id)

    def rename(self, legal_name: LegalName, *, now: datetime) -> tuple[Company, CompanyUpdated | None]:
        """The company under ``legal_name``; unchanged, and no event, when that is already its name."""
        self.require_active()
        if legal_name == self.legal_name:
            return self, None
        renamed = replace(self, legal_name=legal_name, version=self.version + 1, updated_at=now)
        updated = CompanyUpdated(
            scope=self.tenant,
            subject=self.id,
            aggregate_version=renamed.version,
            tax_id=self.tax_id,
            legal_name=legal_name,
        )
        return renamed, updated

    def deactivate(self, *, now: datetime) -> tuple[Company, CompanyDeactivated]:
        """The company deactivated: it changes no more."""
        self.require_active()
        inactive = replace(self, status=CompanyStatus.INACTIVE, version=self.version + 1, updated_at=now)
        return inactive, CompanyDeactivated(
            scope=self.tenant, subject=self.id, aggregate_version=inactive.version
        )
