"""Revision 0004 of identity's schema: a company's fiscal address (Task 35, spec §7.1 as amended 2026-10-26).

Three columns of ``companies``, empty for every company kept (none stated one): the address's line and the
codes of its municipality and its province in its regime's catalogue. A code goes with a line only. Expand
only: nothing is dropped or rewritten, the table's policies and keys stay as they are. Every type and
constraint is spelled out (a revision records what was done and must not move with the models); the tests
keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "identity"


def upgrade() -> None:
    """The address's columns, nullable, and the check that a code goes with a line."""
    op.add_column("companies", sa.Column("address_line", sa.String(300), nullable=True), schema=S)
    op.add_column("companies", sa.Column("address_municipality", sa.String(16), nullable=True), schema=S)
    op.add_column("companies", sa.Column("address_province", sa.String(16), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_companies_address",
        "companies",
        "address_line IS NOT NULL OR (address_municipality IS NULL AND address_province IS NULL)",
        schema=S,
    )


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
