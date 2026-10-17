"""Revision 0001 of schema ``identity``: the chassis's tables, then identity's, under row-level security.

v1's two revisions squashed into one (nothing was in production, §6.5): one active company per tax id in
every organization is the primary key of ``tax_registrations`` from the start, over the tax id's scheme and
value (§7.3: a tax id is qualified by its scheme), a table outside row-level security (§6.3). Every type,
length, word and constraint is spelled out (a revision records what was done and must not move with the
models); the tests keep the models equal to it. The chassis's tables and the row-level security policies
come from their versioned builders, whose output never changes (``chassis_schema_v1``, ``rls.policy``).
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op

from ledger_platform.data import RlsScope, chassis_schema_v1, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "identity"
ISOLATION: Final = (
    ("organizations", RlsScope.ORGANIZATION, "id"),
    ("companies", RlsScope.ORGANIZATION, "organization_id"),
    ("company_tax_ids", RlsScope.ORGANIZATION, "organization_id"),
    ("memberships", RlsScope.ORGANIZATION, "organization_id"),
    ("establishments", RlsScope.ORGANIZATION, "organization_id"),
)
"""Whose rows each table holds, and the column naming the organization (an organization is its own)."""


def _moment(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _of_company(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", "company_id"],
        [f"{S}.companies.organization_id", f"{S}.companies.id"],
        name=f"fk_{table}_company",
    )


def upgrade() -> None:
    """The chassis's tables, identity's, their policies, and the registry of one active company per tax id."""
    chassis_schema_v1(op, S)
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(128), nullable=False),
        _moment("created_at"),
        sa.Column("api_version", sa.Date(), nullable=False),
        schema=S,
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey(f"{S}.organizations.id"), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("subdivision", sa.String(6), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("legal_name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fiscal_profile_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("organization_id", "id", name="uq_companies_organization"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_companies_status"),
        sa.CheckConstraint("version > 0", name="ck_companies_version"),
        schema=S,
    )
    op.create_table(
        "company_tax_ids",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("scheme", sa.String(34), nullable=False),
        sa.Column("value", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "position", name="company_tax_ids_pkey"),
        sa.UniqueConstraint("company_id", "scheme", "value", name="uq_company_tax_ids_value"),
        _of_company("company_tax_ids"),
        sa.CheckConstraint("position >= 0", name="ck_company_tax_ids_position"),
        schema=S,
    )
    op.create_table(
        "tax_registrations",
        sa.Column("scheme", sa.String(34), nullable=False),
        sa.Column("value", sa.String(40), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("scheme", "value", name="tax_registrations_pkey"),
        _of_company("tax_registrations"),
        schema=S,
    )
    op.create_index("ix_tax_registrations_company", "tax_registrations", ["company_id"], schema=S)
    op.create_table(
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
        schema=S,
    )
    op.create_index("ix_memberships_subject", "memberships", ["organization_id", "subject"], schema=S)
    op.create_index("ix_memberships_company", "memberships", ["company_id", "id"], schema=S)
    op.create_table(
        "establishments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        _moment("created_at"),
        sa.UniqueConstraint("company_id", "code", name="uq_establishments_code"),
        _of_company("establishments"),
        schema=S,
    )
    for table, scope, column in ISOLATION:
        for statement in policy(f"{S}.{table}", scope, organization_column=column):
            op.execute(statement)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
