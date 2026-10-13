"""Revision 0002: one RNC or cédula is one taxpayer, so one active company holds it, in every organization.

The per-organization unique constraint becomes a unique index on ``tax_id`` over the active companies only: a
deactivated company frees its tax id. The index enforces it whatever row-level security shows a transaction,
so an organization cannot register a taxpayer another organization's company holds; ``fiscal-receiver`` routes
a document by its RNC to one company unambiguously. Spelled out, as every revision is.

The downgrade puts the per-organization constraint back; it fails, and changes nothing, while one organization
holds two companies of one tax id (an active one beside a deactivated one).
"""

lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the per-organization constraint; index the active companies' tax ids, unique."""
    op.drop_constraint("uq_companies_tax_id", "companies", type_="unique")
    op.create_index(
        "ux_companies_active_tax_id",
        "companies",
        ["tax_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Back to one tax id per organization."""
    op.drop_index("ux_companies_active_tax_id", table_name="companies")
    op.create_unique_constraint("uq_companies_tax_id", "companies", ["organization_id", "tax_id"])
