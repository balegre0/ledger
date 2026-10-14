"""identity's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

Organizations own their companies and the memberships in them (``organization_id``); companies own their
branches and emission points (``organization_id`` and ``company_id``). The migrations put each table under the
isolation of its owner. A row refers to another through a foreign key that carries the tenant
(``(organization_id, company_id)`` to a company, ``(company_id, branch_id)`` to a branch): PostgreSQL's
referential checks bypass row-level security, so a single-column key would let a row of one organization
point at another's company. Every table is in schema ``identity`` (§6.2): identity's role reaches no other.
"""

from datetime import datetime
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.membership import MembershipStatus
from ledger_identity.domain.roles import Role
from ledger_identity.domain.text import MAX_CODE, MAX_LEGAL_NAME, MAX_NAME
from ledger_kernel.access import MAX_PROVIDER_ORGANIZATION_LENGTH, MAX_SUBJECT_LENGTH
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
from ledger_platform.data import ModuleTable

SCHEMA: Final = "identity"
"""identity's schema: its tables and the chassis's (``chassis_schema_v1``), under its roles."""
TAX_SCHEME_LENGTH: Final = 34
"""The longest scheme key ``ledger_kernel.tax_ids`` reads: a country code, a dot and 31 characters."""
ACTIVE_TAX_ID: Final = "ux_companies_active_tax_id"
"""One active company per tax id (its scheme and value), in every organization: a tax id is one taxpayer."""
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


class CompanyRecord(IdentityTable):
    """A company of an organization, at its current version; its tax id is no other active company's."""

    __tablename__: str = "companies"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        Index(
            ACTIVE_TAX_ID,
            "tax_scheme",
            "tax_value",
            unique=True,
            postgresql_where=text(f"status = '{CompanyStatus.ACTIVE.value}'"),
        ),
        _one_of("status", CompanyStatus, name="ck_companies_status"),
        _positive_version("companies"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.organizations.id"))
    tax_scheme: Mapped[str] = mapped_column(String(TAX_SCHEME_LENGTH))
    tax_value: Mapped[str] = mapped_column(String(MAX_TAX_ID_LENGTH))
    legal_name: Mapped[str] = mapped_column(String(MAX_LEGAL_NAME))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class BranchRecord(IdentityTable):
    """A branch of a company."""

    __tablename__: str = "branches"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("company_id", "code", name="uq_branches_code"),
        UniqueConstraint("company_id", "id", name="uq_branches_company"),
        _of_company("branches"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    code: Mapped[str] = mapped_column(String(MAX_CODE))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmissionPointRecord(IdentityTable):
    """An emission point of a branch."""

    __tablename__: str = "emission_points"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("branch_id", "code", name="uq_emission_points_code"),
        Index("ix_emission_points_branch", "branch_id", "id"),
        _of_company("emission_points"),
        ForeignKeyConstraint(
            ["company_id", "branch_id"],
            [f"{SCHEMA}.branches.company_id", f"{SCHEMA}.branches.id"],
            name="fk_emission_points_branch",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    branch_id: Mapped[UUID] = mapped_column()
    code: Mapped[str] = mapped_column(String(MAX_CODE))
    name: Mapped[str] = mapped_column(String(MAX_NAME))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
