"""Revision 0002 of schema ``invoicing``: the fiscal mirror (``fiscal_statuses``, M3b Task 12).

One row per document and fiscal channel: what the channel last reported (its status, its version, the
authority's number and messages, its details under the regime). Under the tenant's row-level security, its key
leading with the tenant, and tied to its document by a key that carries the tenant. Every type, length, word
and constraint is spelled out (a revision records what was done and must not move with the models).
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, policy

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "invoicing"
TENANT: Final = ("organization_id", "company_id", "mode")


def upgrade() -> None:
    """The fiscal mirror and its policies."""
    op.create_table(
        "fiscal_statuses",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("authority_number", sa.String(64), nullable=True),
        sa.Column("messages", postgresql.JSONB(), nullable=False),
        sa.Column("regime", sa.String(6), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*TENANT, "invoice_id", "channel", name="fiscal_statuses_pkey"),
        sa.ForeignKeyConstraint(
            [*TENANT, "invoice_id"],
            [f"{S}.invoices.{column}" for column in (*TENANT, "id")],
            name="fk_fiscal_statuses_invoice",
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_fiscal_statuses_mode"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'accepted_with_observations', 'rejected', 'voided')",
            name="ck_fiscal_statuses_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_fiscal_statuses_version"),
        schema=S,
    )
    for statement in policy(f"{S}.fiscal_statuses", RlsScope.TENANT):
        op.execute(statement)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
