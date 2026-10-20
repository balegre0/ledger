"""Revision 0001 of schema ``invoicing``: the chassis's tables (versions 1 and 2), then invoicing's own.

The module's first two revisions (the chassis's two versions) squashed into one with its tables: nothing was
released (§6.5, as identity's 0001). Every table of a document is under the tenant's row-level security
(``tenant``: organization, company and mode), and every key between them carries the tenant. Every type,
length, word and constraint is spelled out (a revision records what was done and must not move with the
models); the tests keep the models equal to it. The chassis's tables and the policies come from their
versioned builders, whose output never changes (``chassis_schema_v1``, ``chassis_schema_v2``, ``policy``).
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from decimal import Decimal
lazy from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, chassis_schema_v1, chassis_schema_v2, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "invoicing"
TENANT: Final = ("organization_id", "company_id", "mode")
TABLES: Final = ("invoices", "invoice_lines", "invoice_line_taxes", "invoice_taxes")
"""Every table of a document: each under the tenant's policies."""


def _moment(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _tenant() -> tuple[sa.Column[UUID], sa.Column[UUID], sa.Column[str]]:
    return (
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
    )


def _of_invoice(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [*TENANT, "invoice_id"],
        [f"{S}.invoices.{column}" for column in (*TENANT, "id")],
        name=f"fk_{table}_invoice",
    )


def _amount(name: str, *, nullable: bool = False) -> sa.Column[Decimal]:
    return sa.Column(name, sa.Numeric(), nullable=nullable)


def upgrade() -> None:
    """The chassis's tables at version 2, a document's four tables, and their policies."""
    chassis_schema_v1(op, S)
    chassis_schema_v2(op, S)
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_tenant(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=True),
        sa.Column("corrects", sa.Uuid(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        _amount("exchange_rate", nullable=True),
        sa.Column("requested_issue_date", sa.Date(), nullable=True),
        sa.Column("requested_tax_point", sa.Date(), nullable=True),
        sa.Column("requested_establishment", sa.String(10), nullable=True),
        sa.Column("fiscal", postgresql.JSONB(), nullable=False),
        sa.Column("regime", sa.String(6), nullable=False),
        sa.Column("establishment_code", sa.String(10), nullable=False),
        sa.Column("establishment_time_zone", sa.String(64), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("tax_point", sa.Date(), nullable=False),
        _amount("net"),
        _amount("tax"),
        _amount("gross"),
        sa.Column("functional_currency", sa.String(3), nullable=True),
        _amount("functional_net", nullable=True),
        _amount("functional_tax", nullable=True),
        _amount("functional_gross", nullable=True),
        sa.Column("buyer_legal_name", sa.String(300), nullable=True),
        sa.Column("buyer_tax_scheme", sa.String(34), nullable=True),
        sa.Column("buyer_tax_value", sa.String(40), nullable=True),
        sa.Column("buyer_address", sa.String(500), nullable=True),
        sa.Column("buyer_email", sa.String(254), nullable=True),
        sa.Column("seller_legal_name", sa.String(300), nullable=True),
        sa.Column("seller_tax_scheme", sa.String(34), nullable=True),
        sa.Column("seller_tax_value", sa.String(40), nullable=True),
        sa.Column("void_reason", sa.String(500), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _moment("created_at"),
        _moment("updated_at"),
        _moment("issued_at", nullable=True),
        _moment("voided_at", nullable=True),
        sa.UniqueConstraint(*TENANT, "id", name="uq_invoices_tenant"),
        sa.ForeignKeyConstraint(
            [*TENANT, "corrects"],
            [f"{S}.invoices.{column}" for column in (*TENANT, "id")],
            name="fk_invoices_corrects",
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_invoices_mode"),
        sa.CheckConstraint("kind IN ('invoice', 'credit_note', 'debit_note')", name="ck_invoices_kind"),
        sa.CheckConstraint("direction IN ('sales', 'purchase')", name="ck_invoices_direction"),
        sa.CheckConstraint("status IN ('draft', 'issued', 'voided')", name="ck_invoices_status"),
        sa.CheckConstraint("version > 0", name="ck_invoices_version"),
        sa.CheckConstraint("(kind = 'invoice') = (corrects IS NULL)", name="ck_invoices_corrects"),
        sa.CheckConstraint(
            "(status = 'draft') = (seller_legal_name IS NULL) AND (status = 'draft') = (issued_at IS NULL)",
            name="ck_invoices_seller",
        ),
        sa.CheckConstraint("(status = 'voided') = (voided_at IS NOT NULL)", name="ck_invoices_voided"),
        sa.CheckConstraint(
            "num_nulls(seller_legal_name, seller_tax_scheme, seller_tax_value) IN (0, 3)",
            name="ck_invoices_seller_tax_id",
        ),
        sa.CheckConstraint(
            "num_nulls(buyer_tax_scheme, buyer_tax_value) IN (0, 2)", name="ck_invoices_buyer_tax_id"
        ),
        sa.CheckConstraint(
            "buyer_legal_name IS NOT NULL OR num_nonnulls(buyer_tax_scheme, buyer_address, buyer_email) = 0",
            name="ck_invoices_buyer_named",
        ),
        sa.CheckConstraint("void_reason IS NULL OR status = 'voided'", name="ck_invoices_void_reason"),
        sa.CheckConstraint(
            "num_nulls(functional_currency, functional_net, functional_tax, functional_gross) IN (0, 4)",
            name="ck_invoices_functional",
        ),
        schema=S,
    )
    op.create_index(
        "uq_invoices_external_reference",
        "invoices",
        [*TENANT, "kind", "external_reference"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )
    op.create_index(
        "ix_invoices_corrects",
        "invoices",
        [*TENANT, "corrects"],
        schema=S,
        postgresql_where=sa.text("corrects IS NOT NULL"),
    )
    op.create_table(
        "invoice_lines",
        *_tenant(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        _amount("quantity"),
        _amount("unit_price"),
        _amount("discount"),
        _amount("gross"),
        _amount("base"),
        sa.PrimaryKeyConstraint(*TENANT, "invoice_id", "position", name="invoice_lines_pkey"),
        _of_invoice("invoice_lines"),
        sa.CheckConstraint("position >= 0", name="ck_invoice_lines_position"),
        schema=S,
    )
    op.create_table(
        "invoice_line_taxes",
        *_tenant(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("place", sa.SmallInteger(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        _amount("percent"),
        sa.Column("since", sa.Date(), nullable=False),
        _amount("tax"),
        sa.PrimaryKeyConstraint(*TENANT, "invoice_id", "position", "place", name="invoice_line_taxes_pkey"),
        sa.ForeignKeyConstraint(
            [*TENANT, "invoice_id", "position"],
            [f"{S}.invoice_lines.{column}" for column in (*TENANT, "invoice_id", "position")],
            name="fk_invoice_line_taxes_line",
        ),
        sa.CheckConstraint(
            "kind IN ('standard', 'reduced', 'zero', 'exempt', 'outside_scope')",
            name="ck_invoice_line_taxes_kind",
        ),
        sa.CheckConstraint("place >= 0", name="ck_invoice_line_taxes_place"),
        schema=S,
    )
    op.create_table(
        "invoice_taxes",
        *_tenant(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("place", sa.SmallInteger(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        _amount("percent"),
        _amount("base"),
        _amount("tax"),
        _amount("functional_base", nullable=True),
        _amount("functional_tax", nullable=True),
        sa.PrimaryKeyConstraint(*TENANT, "invoice_id", "place", name="invoice_taxes_pkey"),
        _of_invoice("invoice_taxes"),
        sa.CheckConstraint("place >= 0", name="ck_invoice_taxes_place"),
        sa.CheckConstraint(
            "(functional_base IS NULL) = (functional_tax IS NULL)", name="ck_invoice_taxes_functional"
        ),
        schema=S,
    )
    for table in TABLES:
        for statement in policy(f"{S}.{table}", RlsScope.TENANT):
            op.execute(statement)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
