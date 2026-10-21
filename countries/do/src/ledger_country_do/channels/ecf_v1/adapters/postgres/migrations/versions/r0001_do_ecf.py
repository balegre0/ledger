"""Revision 0001 of schema ``do_ecf``: the chassis's tables (both versions), then numbering's, under RLS.

Series, ranges and released numbers, each a company's in one mode (scope ``tenant``). Every type, word and
constraint is spelled out (a revision records what was done and must not move with the models); the tests
keep the models equal to it. Nothing is deleted but a released number once taken: a range accounts for its
numbers for ever (§8.6.3).
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from uuid import UUID

import sqlalchemy as sa
from alembic import op

from ledger_platform.data import RlsScope, chassis_schema_v1, chassis_schema_v2, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
TENANT: Final = ("organization_id", "company_id", "mode")
SERIES: Final = (*TENANT, "environment", "ecf_type")
MODES: Final = "'test', 'live'"
ENVIRONMENTS: Final = "'testecf', 'certecf', 'ecf'"
TYPES: Final = "'31', '32', '33', '34', '41', '43', '44', '45', '46', '47'"


def _tenant() -> tuple[sa.Column[UUID], sa.Column[UUID], sa.Column[str]]:
    return (
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
    )


def _series_words(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(f"mode IN ({MODES})", name=f"ck_{table}_mode"),
        sa.CheckConstraint(f"environment IN ({ENVIRONMENTS})", name=f"ck_{table}_environment"),
        sa.CheckConstraint(f"ecf_type IN ({TYPES})", name=f"ck_{table}_ecf_type"),
    ]


def _of_series(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        list(SERIES), [f"{S}.numbering_series.{column}" for column in SERIES], name=f"fk_{table}_series"
    )


def upgrade() -> None:
    """The chassis's tables, then numbering's, their keys and checks, their policies."""
    chassis_schema_v1(op, S)
    chassis_schema_v2(op, S)
    op.create_table(
        "numbering_series",
        *_tenant(),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(*SERIES, name="numbering_series_pkey"),
        *_series_words("numbering_series"),
        sa.CheckConstraint("version > 0", name="ck_numbering_series_version"),
        schema=S,
    )
    op.create_table(
        "numbering_ranges",
        *_tenant(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("first", sa.BigInteger(), nullable=False),
        sa.Column("last", sa.BigInteger(), nullable=False),
        sa.Column("next", sa.BigInteger(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(*TENANT, "id", name="numbering_ranges_pkey"),
        _of_series("numbering_ranges"),
        *_series_words("numbering_ranges"),
        sa.CheckConstraint("status IN ('active', 'voided')", name="ck_numbering_ranges_status"),
        sa.CheckConstraint(
            "1 <= first AND first <= next AND next <= last + 1 AND last <= 9999999999",
            name="ck_numbering_ranges_numbers",
        ),
        sa.CheckConstraint("version > 0", name="ck_numbering_ranges_version"),
        schema=S,
    )
    op.create_index("numbering_ranges_series", "numbering_ranges", list(SERIES), schema=S)
    op.create_table(
        "released_numbers",
        *_tenant(),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint(*SERIES, "sequence", name="released_numbers_pkey"),
        _of_series("released_numbers"),
        *_series_words("released_numbers"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_released_numbers_sequence"),
        sa.CheckConstraint("available_at >= released_at", name="ck_released_numbers_available"),
        schema=S,
    )
    for table in ("numbering_series", "numbering_ranges", "released_numbers"):
        for statement in policy(f"{S}.{table}", RlsScope.TENANT):
            op.execute(statement)
    for table in ("numbering_series", "numbering_ranges"):
        op.execute(f'REVOKE DELETE ON "{S}".{table} FROM "{S}_rw"')
    op.execute(f'REVOKE UPDATE ON "{S}".released_numbers FROM "{S}_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
