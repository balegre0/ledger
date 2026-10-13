"""Revision 0001, from nothing: identity's first schema.

The chassis's tables, then organizations, companies, memberships, branches and emission points, every table
that holds a tenant's data under row-level security. Every type, length and word is spelled out rather than
read from the models: a revision records what was done and must not change when a model does (the tests keep
the two equal).
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from ledger_platform.database import organization_isolation, tenant_isolation
from ledger_platform.schema import CHASSIS_POLICIES

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEWEST_FIRST: Final = (
    "emission_points",
    "branches",
    "memberships",
    "companies",
    "organizations",
    "idempotency_keys",
    "inbox",
    "outbox",
)
"""Every table this revision creates, each before the tables it refers to."""


def _apply(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(statement)


def _moment(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _now(name: str) -> sa.Column[datetime]:
    """A moment the database stamps itself when the row is written without it."""
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _of_company(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", "company_id"],
        ["companies.organization_id", "companies.id"],
        name=f"fk_{table}_company",
    )


def _chassis() -> None:
    """The platform's tables, as ``ledger_platform`` declares them, and their row-level security."""
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("headers", JSONB(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        _moment("created_at"),
        _now("stored_at"),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _now("next_attempt_at"),
    )
    op.create_index(
        "ix_outbox_due", "outbox", [sa.text("(attempts > 0)"), "created_at", "id", "next_attempt_at"]
    )
    op.create_index("ix_outbox_stored_at", "outbox", ["stored_at"])
    op.create_table(
        "inbox",
        sa.Column("consumer", sa.String(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        _moment("processed_at"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("headers", JSONB(), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        _moment("expires_at"),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["organization_id", "expires_at"])
    _apply(CHASSIS_POLICIES)


def upgrade() -> None:
    """Create the chassis's tables and identity's, and put every tenant's data under row-level security."""
    _chassis()
    organizations = op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(128), nullable=False),
        _moment("created_at"),
    )
    companies = op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("tax_id", sa.String(11), nullable=False),
        sa.Column("legal_name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("organization_id", "tax_id", name="uq_companies_tax_id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_companies_status"),
        sa.CheckConstraint("version > 0", name="ck_companies_version"),
    )
    memberships = op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("changed_at"),
        sa.Column("changed_by", sa.String(128), nullable=False),
        sa.UniqueConstraint("company_id", "subject", name="uq_memberships_subject"),
        _of_company("memberships"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'accountant', 'issuer', 'viewer')", name="ck_memberships_role"
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_memberships_status"),
        sa.CheckConstraint("version > 0", name="ck_memberships_version"),
    )
    op.create_index("ix_memberships_subject", "memberships", ["organization_id", "subject"])
    branches = op.create_table(
        "branches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("company_id", "code", name="uq_branches_code"),
        sa.UniqueConstraint("company_id", "id", name="uq_branches_company"),
        _of_company("branches"),
    )
    emission_points = op.create_table(
        "emission_points",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("branch_id", "code", name="uq_emission_points_code"),
        _of_company("emission_points"),
        sa.ForeignKeyConstraint(
            ["company_id", "branch_id"],
            ["branches.company_id", "branches.id"],
            name="fk_emission_points_branch",
        ),
    )
    _apply(organization_isolation(organizations, column="id"))
    _apply(organization_isolation(companies))
    _apply(organization_isolation(memberships))
    _apply(tenant_isolation(branches))
    _apply(tenant_isolation(emission_points))


def downgrade() -> None:
    """Drop every table this revision created; their indexes and policies go with them."""
    for table in _NEWEST_FIRST:
        op.drop_table(table)
