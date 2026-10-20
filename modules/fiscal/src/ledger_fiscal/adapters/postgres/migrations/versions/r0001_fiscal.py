"""Revision 0001 of schema ``fiscal``: the chassis's tables (both versions), then ``enrollments``, under RLS.

An enrollment is a company's in one mode (scope ``tenant``); one that is not disabled per company, mode and
channel (§7.6). Every type, word and constraint is spelled out (a revision records what was done and must not
move with the models); the tests keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, chassis_schema_v1, chassis_schema_v2, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "fiscal"


def upgrade() -> None:
    """The chassis's tables, ``enrollments``, one not disabled per company, mode and channel, policies."""
    chassis_schema_v1(op, S)
    chassis_schema_v2(op, S)
    op.create_table(
        "enrollments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("currently_due", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "company_id", "mode", "id", name="enrollments_pkey"),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_enrollments_mode"),
        sa.CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_enrollments_status"),
        sa.CheckConstraint("version > 0", name="ck_enrollments_version"),
        sa.CheckConstraint(
            "status <> 'disabled' OR (disabled_reason IS NOT NULL AND cardinality(currently_due) = 0)",
            name="ck_enrollments_disabled",
        ),
        schema=S,
    )
    op.create_index(
        "enrollments_one_per_channel",
        "enrollments",
        ["company_id", "mode", "channel"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("status <> 'disabled'"),
    )
    for statement in policy(f"{S}.enrollments", RlsScope.TENANT):
        op.execute(statement)
    op.execute('REVOKE DELETE ON "fiscal".enrollments FROM "fiscal_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
