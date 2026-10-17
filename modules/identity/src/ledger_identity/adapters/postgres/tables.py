"""identity's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

Organizations own their companies and what belongs to them: tax ids, memberships, establishments and API
clients (``organization_id``, and ``company_id`` for the company a row is of). The migrations put each table
under the isolation of its organization. A row refers to another through a foreign key that carries the tenant
(``(organization_id, company_id)`` to a company): PostgreSQL's referential checks bypass row-level security,
so a single-column key would let a row of one organization point at another's company. Every table is in
schema ``identity`` (§6.2): identity's role reaches no other.
"""

from datetime import date, datetime
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_identity.domain.api_clients import ApiClientStatus
from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.membership import MembershipStatus
from ledger_identity.domain.text import MAX_CODE, MAX_LEGAL_NAME, MAX_NAME
from ledger_kernel.access import MAX_PROVIDER_ORGANIZATION_LENGTH, MAX_SUBJECT_LENGTH, Role
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
from ledger_kernel.tenancy import Mode
from ledger_platform.data import ModuleTable

SCHEMA: Final = "identity"
"""identity's schema: its tables and the chassis's (``chassis_schema_v1``), under its roles."""
TAX_SCHEME_LENGTH: Final = 34
"""The longest scheme key ``ledger_kernel.tax_ids`` reads: a country code, a dot and 31 characters."""
COUNTRY_LENGTH: Final = 2
"""An ISO 3166-1 alpha-2 code."""
SUBDIVISION_LENGTH: Final = 6
"""An ISO 3166-2 code as the kernel reads it: the country, a dash and up to three characters."""
CURRENCY_LENGTH: Final = 3
"""An ISO 4217 code."""
TIME_ZONE_LENGTH: Final = 64
"""The longest IANA zone name ``ledger_identity.domain.text`` reads."""
WORD_LENGTH: Final = 16
"""For a status or a role: a short word of a closed list, which a check constraint names."""


def _one_of(column: str, words: type[StrEnum], *, name: str) -> CheckConstraint:
    """``column`` holds one of ``words``' values: the domain's list, which the migration spells out."""
    allowed = ", ".join(f"'{word}'" for word in words)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _positive_version(table: str) -> CheckConstraint:
    return CheckConstraint("version > 0", name=f"ck_{table}_version")


def _of_company(table: str) -> ForeignKeyConstraint:
    """The key from ``table``'s ``(organization_id, company_id)`` to its company: the tenant is part of it."""
    return ForeignKeyConstraint(
        ["organization_id", "company_id"],
        [f"{SCHEMA}.companies.organization_id", f"{SCHEMA}.companies.id"],
        name=f"fk_{table}_company",
    )


class IdentityTable(ModuleTable, schema=SCHEMA):
    """identity's own tables, in its schema; the chassis's are ``chassis_tables("identity")``."""


class OrganizationRecord(IdentityTable):
    """An organization, written with its first company."""

    __tablename__: str = "organizations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(MAX_PROVIDER_ORGANIZATION_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    api_version: Mapped[date] = mapped_column(Date)


class CompanyRecord(IdentityTable):
    """A company of an organization, at its current version (its tax id is held in ``tax_registrations``)."""

    __tablename__: str = "companies"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        _one_of("status", CompanyStatus, name="ck_companies_status"),
        _positive_version("companies"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id"))
    country: Mapped[str] = mapped_column(String(COUNTRY_LENGTH))
    subdivision: Mapped[str | None] = mapped_column(String(SUBDIVISION_LENGTH))
    currency: Mapped[str] = mapped_column(String(CURRENCY_LENGTH))
    legal_name: Mapped[str] = mapped_column(String(MAX_LEGAL_NAME))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    fiscal_profile_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanyTaxIdRecord(IdentityTable):
    """One of a company's tax ids, at its place in the company's list (the first is the primary one).

    Kept for as long as the company is: a deactivated company still shows its tax ids, though its registry
    rows (``tax_registrations``) are released.
    """

    __tablename__: str = "company_tax_ids"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "scheme", "value", name="uq_company_tax_ids_value"),
        _of_company("company_tax_ids"),
        CheckConstraint("position >= 0", name="ck_company_tax_ids_position"),
    )

    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    scheme: Mapped[str] = mapped_column(String(TAX_SCHEME_LENGTH))
    value: Mapped[str] = mapped_column(String(MAX_TAX_ID_LENGTH))


class TaxRegistrationRecord(IdentityTable):
    """The tax id an active company holds: one taxpayer is one active company, in every organization (§7.3).

    The global registry, outside row-level security by design (§6.3): the one thing identity keeps across
    organizations, and the table a future cell router and the receiver look a taxpayer up by. A company's row
    here lives while the company is active; its deactivation releases the tax id.
    """

    __tablename__: str = "tax_registrations"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_company("tax_registrations"),
        Index("ix_tax_registrations_company", "company_id"),
    )

    scheme: Mapped[str] = mapped_column(String(TAX_SCHEME_LENGTH), primary_key=True)
    value: Mapped[str] = mapped_column(String(MAX_TAX_ID_LENGTH), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()


class MembershipRecord(IdentityTable):
    """A principal's role in a company, or the one it held until it was revoked."""

    __tablename__: str = "memberships"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "subject", name="uq_memberships_subject"),
        _of_company("memberships"),
        _one_of("role", Role, name="ck_memberships_role"),
        _one_of("status", MembershipStatus, name="ck_memberships_status"),
        _positive_version("memberships"),
        Index("ix_memberships_subject", "organization_id", "subject"),
        Index("ix_memberships_company", "company_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    subject: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))
    role: Mapped[str] = mapped_column(String(WORD_LENGTH))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    version: Mapped[int] = mapped_column(BigInteger)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))


class ApiClientRecord(IdentityTable):
    """An API client: a machine user of an organization for one mode, or the one it was until revoked.

    Under the organization's row-level security in one mode: a unit sees the clients of its mode only. One
    machine user is one active client of an organization (the partial unique index), whatever its mode.
    """

    __tablename__: str = "api_clients"
    __table_args__: tuple[SchemaItem, ...] = (
        _one_of("mode", Mode, name="ck_api_clients_mode"),
        _one_of("status", ApiClientStatus, name="ck_api_clients_status"),
        Index(
            "uq_api_clients_active_subject",
            "organization_id",
            "subject",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_api_clients_subject", "organization_id", "subject"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id"))
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH))
    subject: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    api_version: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(MAX_SUBJECT_LENGTH))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EstablishmentRecord(IdentityTable):
    """An establishment of a company, known by a code unique in the company."""

    __tablename__: str = "establishments"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "code", name="uq_establishments_code"),
        _of_company("establishments"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    code: Mapped[str] = mapped_column(String(MAX_CODE))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    time_zone: Mapped[str] = mapped_column(String(TIME_ZONE_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
