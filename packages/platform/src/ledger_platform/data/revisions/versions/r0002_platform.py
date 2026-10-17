"""Revision 0002 of the platform schema: the chassis's second version (the job queue's housekeeping)."""

lazy from collections.abc import Sequence

from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v2

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``chassis_schema_v2`` in ``platform``."""
    chassis_schema_v2(op, "platform")


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
