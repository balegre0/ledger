"""A company: one taxpayer of an organization, and the unit of tenancy (§7).

It is in one jurisdiction, keeps its books in one functional currency and holds one or more tax ids, the
first of which is its primary one. Once its fiscal profile is locked (before its first numbered document) its
jurisdiction, functional currency and primary tax id never change (§7.1: another taxpayer is another company).
Every change makes a new version, which is the company's ``ETag``. A deactivated company changes no more; its
data stays readable.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import final
lazy from datetime import datetime

from ledger_identity.domain.errors import CompanyInactiveError
from ledger_identity.events import CompanyDeactivated, CompanyRegistered, CompanyUpdated
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.tenancy import Tenant
lazy from ledger_identity.domain.text import LegalName
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.tax_ids import TaxId


class CompanyStatus(StrEnum):
    """Whether a company can still change."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalProfile:
    """What a taxpayer is to its tax authority and how its books are kept (§7.1).

    The jurisdiction it answers to, the functional currency its books are kept in, and its tax ids: one or
    more, distinct, the first its primary one. A company's profile is locked before its first numbered
    document.
    """

    jurisdiction: Jurisdiction
    functional_currency: Currency
    tax_ids: tuple[TaxId, ...]

    def __post_init__(self) -> None:
        if not self.tax_ids or len(set(self.tax_ids)) != len(self.tax_ids):
            raise InvalidValueError(
                field="fiscal_profile.tax_ids", requirement="one or more distinct tax ids"
            )

    @property
    def primary_tax_id(self) -> TaxId:
        """The first of its tax ids: the one the taxpayer is known by."""
        return self.tax_ids[0]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Company:
    """A company and its version: each method returns the company changed and the event that says so."""

    id: CompanyId
    organization: OrganizationId
    profile: FiscalProfile
    legal_name: LegalName
    status: CompanyStatus
    fiscal_profile_locked_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidValueError(field="company.version", requirement="at least 1")

    @property
    def tenant(self) -> Tenant:
        """The company with its organization: what its rows and its events are scoped to."""
        return Tenant(self.organization, self.id, None)

    @classmethod
    def register(
        cls, *, organization: OrganizationId, profile: FiscalProfile, legal_name: LegalName, now: datetime
    ) -> tuple[Company, CompanyRegistered]:
        """A new company of ``organization``, active, at version 1, its profile not yet locked."""
        company = cls(
            id=CompanyId(new_id()),
            organization=organization,
            profile=profile,
            legal_name=legal_name,
            status=CompanyStatus.ACTIVE,
            fiscal_profile_locked_at=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        registered = CompanyRegistered(
            scope=company.tenant,
            subject=company.id,
            aggregate_version=1,
            jurisdiction=profile.jurisdiction,
            functional_currency=profile.functional_currency,
            tax_ids=profile.tax_ids,
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
        return renamed, renamed.updated()

    def lock_fiscal_profile(self, *, now: datetime) -> tuple[Company, CompanyUpdated | None]:
        """The company with its fiscal profile locked; unchanged, and no event, when it already is.

        Locked before its first numbered document: jurisdiction, functional currency and primary tax id are
        what every number and every tax point is judged by, so they cannot change afterwards.
        """
        self.require_active()
        if self.fiscal_profile_locked_at is not None:
            return self, None
        locked = replace(self, fiscal_profile_locked_at=now, version=self.version + 1, updated_at=now)
        return locked, locked.updated()

    def updated(self) -> CompanyUpdated:
        """The event that says the company is at its current version."""
        return CompanyUpdated(
            scope=self.tenant,
            subject=self.id,
            aggregate_version=self.version,
            tax_ids=self.profile.tax_ids,
            legal_name=self.legal_name,
            fiscal_profile_locked=self.fiscal_profile_locked_at is not None,
        )

    def deactivate(self, *, now: datetime) -> tuple[Company, CompanyDeactivated]:
        """The company deactivated: it changes no more."""
        self.require_active()
        inactive = replace(self, status=CompanyStatus.INACTIVE, version=self.version + 1, updated_at=now)
        return inactive, CompanyDeactivated(
            scope=self.tenant, subject=self.id, aggregate_version=inactive.version
        )
