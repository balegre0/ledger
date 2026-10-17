"""Revision 0002 of schema ``invoicing``: the chassis's second version (the job queue's housekeeping)."""

lazy from collections.abc import Sequence

from alembic import op

from ledger_platform.data import chassis_schema_v2

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``chassis_schema_v2`` in ``invoicing``."""
    chassis_schema_v2(op, "invoicing")


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
