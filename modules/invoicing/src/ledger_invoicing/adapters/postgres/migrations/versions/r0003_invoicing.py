"""Revision 0003 of schema ``invoicing``: the seller's fiscal address (Task 35; spec §7.5, 2026-10-26).

A document is issued with its seller as identity has the company then; the company's fiscal address is now
part of that snapshot: its line and the codes of its municipality and its province. Three columns of
``invoices``, empty for every document kept (issued before the company could state an address, so it was
issued with none); an address goes with a seller, a code with a line. Expand only: nothing is dropped or
rewritten. Every type and constraint is spelled out (a revision records what was done and must not move with
the models); the tests keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "invoicing"


def upgrade() -> None:
    """The seller's address columns, nullable, and the check that holds them together."""
    op.add_column("invoices", sa.Column("seller_address_line", sa.String(300), nullable=True), schema=S)
    op.add_column(
        "invoices", sa.Column("seller_address_municipality", sa.String(16), nullable=True), schema=S
    )
    op.add_column("invoices", sa.Column("seller_address_province", sa.String(16), nullable=True), schema=S)
    op.create_check_constraint(
        "ck_invoices_seller_address",
        "invoices",
        (
            "(seller_legal_name IS NOT NULL OR seller_address_line IS NULL)"
            " AND (seller_address_line IS NOT NULL"
            " OR num_nonnulls(seller_address_municipality, seller_address_province) = 0)"
        ),
        schema=S,
    )


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
