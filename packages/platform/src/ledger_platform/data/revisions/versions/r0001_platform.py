"""Revision 0001 of the platform schema: the chassis's tables (its schedules enqueue jobs), then its own."""

lazy from collections.abc import Sequence

from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1, platform_schema_v1

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``chassis_schema_v1`` in ``platform``, then ``routing`` and ``schedule_leases``."""
    chassis_schema_v1(op, "platform")
    platform_schema_v1(op)


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
