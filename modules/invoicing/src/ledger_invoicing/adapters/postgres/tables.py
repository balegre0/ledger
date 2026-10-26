"""invoicing's tables, typed for SQLAlchemy Core: what the migrations create and the repositories use.

A document belongs to a company in a mode, and so does every row of it: they are under the tenant's row-level
security (``organization_id``, ``company_id``, ``mode``), every key leads with the tenant, and a foreign key
between rows carries it (PostgreSQL's referential checks bypass row-level security, so a key without the
tenant would let a row of one tenant point at another's). Every table is in schema ``invoicing`` (§6.2):
invoicing's role reaches no other.

A document is one row of ``invoices`` (its terms, dates, parties and totals), a row of ``invoice_lines`` per
line (as written and as priced), a row of ``invoice_line_taxes`` per tax a line is charged (the rate resolved
at the tax point, kept for ever) and a row of ``invoice_taxes`` per category of its totals (with the
functional amounts of a document in another currency). What each fiscal channel last reported of a document
is a row of ``fiscal_statuses`` (the fiscal mirror). Rows keep their place in the document
(``position``, ``place``): the order is the document's, never a collation's. Amounts are ``numeric`` with
their own scale (§6.6) and dates are the document's legal dates, never instants.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID
lazy from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from ledger_invoicing.domain.fiscal import MAX_AUTHORITY_NUMBER, MAX_ENVIRONMENT, FiscalStatusName
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_invoicing.domain.text import (
    MAX_ADDRESS,
    MAX_DESCRIPTION,
    MAX_EMAIL,
    MAX_EXTERNAL_REFERENCE,
    MAX_LEGAL_NAME,
    MAX_REASON,
)
from ledger_kernel.addresses import MAX_ADDRESS_LINE
from ledger_kernel.regimes import Direction, DocumentKind, TaxCategoryKind
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH
from ledger_kernel.tenancy import Mode
from ledger_platform.data import ModuleTable

SCHEMA: Final = "invoicing"
"""invoicing's schema: its tables and the chassis's (``chassis_schema_v1`` and ``_v2``), under its roles."""
WORD_LENGTH: Final = 16
"""For a mode, a kind, a direction or a status: a word of a closed list, which a check constraint names."""
CURRENCY_LENGTH: Final = 3
"""An ISO 4217 code."""
REGIME_LENGTH: Final = 6
"""A regime's key: its country (``do``) or its subdivision (``us-ca``), as the kernel reads them."""
CODE_LENGTH: Final = 10
"""An establishment's code, as identity keeps it: 1 to 10 upper-case letters and digits."""
TIME_ZONE_LENGTH: Final = 64
"""An IANA zone name, as identity keeps it."""
TAX_SCHEME_LENGTH: Final = 34
"""The longest scheme key ``ledger_kernel.tax_ids`` reads: a country code, a dot and 31 characters."""
SUBDIVISION_CODE_LENGTH: Final = 16
"""The longest code of a subdivisions' catalogue the kernel reads (``ledger_kernel.addresses``)."""
SELLER_ADDRESS_CHECK: Final = (
    "(seller_legal_name IS NOT NULL OR seller_address_line IS NULL)"
    " AND (seller_address_line IS NOT NULL"
    " OR num_nonnulls(seller_address_municipality, seller_address_province) = 0)"
)
"""A seller's address goes with a seller, and its codes with its line (revision 0003 spells it out)."""
CHANNEL_LENGTH: Final = 64
"""A channel's key (``do-ecf-v1``), as manifests name it."""
STATUS_LENGTH: Final = 32
"""A fiscal status's word (``accepted_with_observations``)."""
TENANT: Final = ("organization_id", "company_id", "mode")
"""What every row is scoped by, and what every key between rows carries."""


def _one_of(column: str, words: type[StrEnum], *, name: str) -> CheckConstraint:
    """``column`` holds one of ``words``' values: the domain's list, which the migration spells out."""
    allowed = ", ".join(f"'{word}'" for word in words)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _of_invoice(table: str) -> ForeignKeyConstraint:
    """The key from ``table``'s tenant and ``invoice_id`` to its document: the tenant is part of it."""
    return ForeignKeyConstraint(
        [*TENANT, "invoice_id"],
        [f"{SCHEMA}.invoices.{column}" for column in (*TENANT, "id")],
        name=f"fk_{table}_invoice",
    )


class InvoicingTable(ModuleTable, schema=SCHEMA):
    """invoicing's own tables, in its schema; the chassis's are ``chassis_tables("invoicing")``."""


class InvoiceRecord(InvoicingTable):
    """A document at its current version: its terms, its pricing, its parties and its totals.

    ``requested_*`` are the terms as the client wrote them (``None``: the default), which a draft is priced
    with again when it is issued; ``issue_date``, ``tax_point``, ``regime`` and the establishment are what
    they were priced as. The ``functional_*`` totals are those of a document in another currency than the
    company's functional one, and only then set. The seller is set once the document is issued, with its
    fiscal address if the company stated one then (revision 0003): a code goes with a line only.
    """

    __tablename__: str = "invoices"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(*TENANT, "id", name="uq_invoices_tenant"),
        ForeignKeyConstraint(
            [*TENANT, "corrects"],
            [f"{SCHEMA}.invoices.{column}" for column in (*TENANT, "id")],
            name="fk_invoices_corrects",
        ),
        _one_of("mode", Mode, name="ck_invoices_mode"),
        _one_of("kind", DocumentKind, name="ck_invoices_kind"),
        _one_of("direction", Direction, name="ck_invoices_direction"),
        _one_of("status", InvoiceStatus, name="ck_invoices_status"),
        CheckConstraint("version > 0", name="ck_invoices_version"),
        CheckConstraint("(kind = 'invoice') = (corrects IS NULL)", name="ck_invoices_corrects"),
        CheckConstraint(
            "(status = 'draft') = (seller_legal_name IS NULL) AND (status = 'draft') = (issued_at IS NULL)",
            name="ck_invoices_seller",
        ),
        CheckConstraint("(status = 'voided') = (voided_at IS NOT NULL)", name="ck_invoices_voided"),
        CheckConstraint(
            "num_nulls(seller_legal_name, seller_tax_scheme, seller_tax_value) IN (0, 3)",
            name="ck_invoices_seller_tax_id",
        ),
        CheckConstraint(
            "num_nulls(buyer_tax_scheme, buyer_tax_value) IN (0, 2)", name="ck_invoices_buyer_tax_id"
        ),
        CheckConstraint(SELLER_ADDRESS_CHECK, name="ck_invoices_seller_address"),
        CheckConstraint(
            "buyer_legal_name IS NOT NULL OR num_nonnulls(buyer_tax_scheme, buyer_address, buyer_email) = 0",
            name="ck_invoices_buyer_named",
        ),
        CheckConstraint("void_reason IS NULL OR status = 'voided'", name="ck_invoices_void_reason"),
        CheckConstraint(
            "num_nulls(functional_currency, functional_net, functional_tax, functional_gross) IN (0, 4)",
            name="ck_invoices_functional",
        ),
        Index(
            "uq_invoices_external_reference",
            *TENANT,
            "kind",
            "external_reference",
            unique=True,
            postgresql_where=text("external_reference IS NOT NULL"),
        ),
        Index("ix_invoices_corrects", *TENANT, "corrects", postgresql_where=text("corrects IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column()
    company_id: Mapped[UUID] = mapped_column()
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH))
    kind: Mapped[str] = mapped_column(String(WORD_LENGTH))
    direction: Mapped[str] = mapped_column(String(WORD_LENGTH))
    status: Mapped[str] = mapped_column(String(WORD_LENGTH))
    external_reference: Mapped[str | None] = mapped_column(String(MAX_EXTERNAL_REFERENCE))
    corrects: Mapped[UUID | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(CURRENCY_LENGTH))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric())
    requested_issue_date: Mapped[date | None] = mapped_column(Date)
    requested_tax_point: Mapped[date | None] = mapped_column(Date)
    requested_establishment: Mapped[str | None] = mapped_column(String(CODE_LENGTH))
    fiscal: Mapped[dict[str, object]] = mapped_column(JSONB)
    """The country fields as sent (``fiscal.<regime>.<field>``)."""
    regime: Mapped[str] = mapped_column(String(REGIME_LENGTH))
    establishment_code: Mapped[str] = mapped_column(String(CODE_LENGTH))
    establishment_time_zone: Mapped[str] = mapped_column(String(TIME_ZONE_LENGTH))
    issue_date: Mapped[date] = mapped_column(Date)
    tax_point: Mapped[date] = mapped_column(Date)
    net: Mapped[Decimal] = mapped_column(Numeric())
    tax: Mapped[Decimal] = mapped_column(Numeric())
    gross: Mapped[Decimal] = mapped_column(Numeric())
    functional_currency: Mapped[str | None] = mapped_column(String(CURRENCY_LENGTH))
    functional_net: Mapped[Decimal | None] = mapped_column(Numeric())
    functional_tax: Mapped[Decimal | None] = mapped_column(Numeric())
    functional_gross: Mapped[Decimal | None] = mapped_column(Numeric())
    buyer_legal_name: Mapped[str | None] = mapped_column(String(MAX_LEGAL_NAME))
    buyer_tax_scheme: Mapped[str | None] = mapped_column(String(TAX_SCHEME_LENGTH))
    buyer_tax_value: Mapped[str | None] = mapped_column(String(MAX_TAX_ID_LENGTH))
    buyer_address: Mapped[str | None] = mapped_column(String(MAX_ADDRESS))
    buyer_email: Mapped[str | None] = mapped_column(String(MAX_EMAIL))
    seller_legal_name: Mapped[str | None] = mapped_column(String(MAX_LEGAL_NAME))
    seller_tax_scheme: Mapped[str | None] = mapped_column(String(TAX_SCHEME_LENGTH))
    seller_tax_value: Mapped[str | None] = mapped_column(String(MAX_TAX_ID_LENGTH))
    seller_address_line: Mapped[str | None] = mapped_column(String(MAX_ADDRESS_LINE))
    seller_address_municipality: Mapped[str | None] = mapped_column(String(SUBDIVISION_CODE_LENGTH))
    seller_address_province: Mapped[str | None] = mapped_column(String(SUBDIVISION_CODE_LENGTH))
    void_reason: Mapped[str | None] = mapped_column(String(MAX_REASON))
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvoiceLineRecord(InvoicingTable):
    """A line of a document at its ``position``: as the client wrote it, and priced (its gross and base)."""

    __tablename__: str = "invoice_lines"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_invoice("invoice_lines"),
        CheckConstraint("position >= 0", name="ck_invoice_lines_position"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    description: Mapped[str] = mapped_column(String(MAX_DESCRIPTION))
    quantity: Mapped[Decimal] = mapped_column(Numeric())
    unit_price: Mapped[Decimal] = mapped_column(Numeric())
    discount: Mapped[Decimal] = mapped_column(Numeric())
    gross: Mapped[Decimal] = mapped_column(Numeric())
    base: Mapped[Decimal] = mapped_column(Numeric())


class InvoiceLineTaxRecord(InvoicingTable):
    """A tax line ``position`` is charged, at its ``place`` among them: the rate resolved, and the tax."""

    __tablename__: str = "invoice_line_taxes"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            [*TENANT, "invoice_id", "position"],
            [f"{SCHEMA}.invoice_lines.{column}" for column in (*TENANT, "invoice_id", "position")],
            name="fk_invoice_line_taxes_line",
        ),
        _one_of("kind", TaxCategoryKind, name="ck_invoice_line_taxes_kind"),
        CheckConstraint("place >= 0", name="ck_invoice_line_taxes_place"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    place: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    category: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(WORD_LENGTH))
    percent: Mapped[Decimal] = mapped_column(Numeric())
    since: Mapped[date] = mapped_column(Date)
    tax: Mapped[Decimal] = mapped_column(Numeric())


class InvoiceTaxRecord(InvoicingTable):
    """A category of a document's totals at its ``place``: its base and tax, and the functional currency's."""

    __tablename__: str = "invoice_taxes"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_invoice("invoice_taxes"),
        CheckConstraint("place >= 0", name="ck_invoice_taxes_place"),
        CheckConstraint(
            "(functional_base IS NULL) = (functional_tax IS NULL)", name="ck_invoice_taxes_functional"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    place: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    category: Mapped[str] = mapped_column(Text)
    percent: Mapped[Decimal] = mapped_column(Numeric())
    base: Mapped[Decimal] = mapped_column(Numeric())
    tax: Mapped[Decimal] = mapped_column(Numeric())
    functional_base: Mapped[Decimal | None] = mapped_column(Numeric())
    functional_tax: Mapped[Decimal | None] = mapped_column(Numeric())


class FiscalStatusRecord(InvoicingTable):
    """The fiscal mirror: what a channel last reported of a document, one row per document and channel.

    ``version`` is the channel's (a newer one replaces the row); ``details`` are the channel's words under the
    regime (``regime``), shown on the wire and never read here.
    """

    __tablename__: str = "fiscal_statuses"
    __table_args__: tuple[SchemaItem, ...] = (
        _of_invoice("fiscal_statuses"),
        _one_of("mode", Mode, name="ck_fiscal_statuses_mode"),
        _one_of("status", FiscalStatusName, name="ck_fiscal_statuses_status"),
        CheckConstraint("version > 0", name="ck_fiscal_statuses_version"),
    )

    organization_id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(WORD_LENGTH), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(CHANNEL_LENGTH), primary_key=True)
    environment: Mapped[str] = mapped_column(String(MAX_ENVIRONMENT))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    version: Mapped[int] = mapped_column(BigInteger)
    authority_number: Mapped[str | None] = mapped_column(String(MAX_AUTHORITY_NUMBER))
    messages: Mapped[list[object]] = mapped_column(JSONB)
    regime: Mapped[str] = mapped_column(String(REGIME_LENGTH))
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
