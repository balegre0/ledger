"""Revision 0002 of schema ``fiscal``: ``enrollments.review_due``, and ``routings``, under RLS (Task 20).

An enrollment is marked ``review_due`` when its channel says what it requires of the company changed
(Decision 34); a routing is a document sent to a channel for one of the company's enrollments, one per
document and channel (§7.6), never changed or deleted. Every type, word and constraint is spelled out (a
revision records what was done and must not move with the models); the tests keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ledger_platform.data import RlsScope, policy

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "fiscal"


def upgrade() -> None:
    """``review_due`` (false for every enrollment kept), then ``routings`` and its policies."""
    op.add_column(
        "enrollments",
        sa.Column("review_due", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=S,
    )
    op.create_table(
        "routings",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "company_id", "mode", "invoice_id", "channel", name="routings_pkey"
        ),
        sa.ForeignKeyConstraint(
            ("organization_id", "company_id", "mode", "enrollment_id"),
            (
                "fiscal.enrollments.organization_id",
                "fiscal.enrollments.company_id",
                "fiscal.enrollments.mode",
                "fiscal.enrollments.id",
            ),
            name="fk_routings_enrollment",
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_routings_mode"),
        schema=S,
    )
    for statement in policy(f"{S}.routings", RlsScope.TENANT, append_only=True):
        op.execute(statement)
    op.execute('REVOKE UPDATE, DELETE ON "fiscal".routings FROM "fiscal_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
