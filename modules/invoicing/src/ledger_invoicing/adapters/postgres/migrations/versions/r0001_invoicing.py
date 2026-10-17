"""Revision 0001 of schema ``invoicing``: the chassis's tables (version 1)."""

lazy from collections.abc import Sequence

from alembic import op

from ledger_platform.data import chassis_schema_v1

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``chassis_schema_v1`` in ``invoicing``."""
    chassis_schema_v1(op, "invoicing")


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
