"""invoicing's repository: the ``Invoices`` port over one transaction of a tenant (SQLAlchemy Core).

The repository is bound to the unit's tenant (organization, company and mode) and names it in every read and
write; row-level security enforces it again, so tenancy is checked twice (§2): a statement that forgot its
filter still sees nothing of another tenant, and a policy dropped by mistake still leaves every read and
change inside the tenant. A document of another tenant handed to it to keep is refused before any statement
(``ForeignTenantError``): a defect of the caller.

A document is kept as its row, its lines, their taxes and its totals' categories (``tables``), read back with
one statement per table for any number of documents. A new document whose business key a document of its
type holds is not kept: the insert does nothing on the unique index's conflict (``ON CONFLICT DO NOTHING``)
and the holder is then read, never a unique violation caught (which would abort the unit's transaction). A
change replaces the version the unit holds locked, the lines and totals with it: issuing a draft prices it
again. Rows are read into typed rows (``_InvoiceRow`` and the others), checked as they are read.

The fiscal mirror (``PgFiscalMirror``) keeps, per document and channel, the newest status a channel reported.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Final, final, override
lazy from collections.abc import Mapping, Sequence
lazy from datetime import date, datetime
lazy from decimal import Decimal
lazy from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy import ColumnElement, Select
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_invoicing.adapters.postgres.tables import (
    FiscalStatusRecord,
    InvoiceLineRecord,
    InvoiceLineTaxRecord,
    InvoiceRecord,
    InvoiceTaxRecord,
)
from ledger_invoicing.application.ports import FiscalMirror, ForeignTenantError, Invoices, StaleWriteError
from ledger_invoicing.domain.fiscal import ACCEPTANCES, Applied, AuthorityMessage, FiscalStatus
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Party, Seller
from ledger_invoicing.domain.pricing import EstablishmentRef, LineTax, PricedLine, Pricing, Terms
from ledger_invoicing.domain.text import Address, Email, ExternalReference, LegalName, Reason
from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.money import Currency, Money
from ledger_kernel.pagination import page_of
from ledger_kernel.regimes import CategoryTotal, RegimeKey, TaxCategoryKey, Totals
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Tenant
from ledger_platform.data import paged
lazy from ledger_invoicing.application.ports import InvoiceFilters
lazy from ledger_invoicing.domain.fiscal import Detail, FiscalStatusName
lazy from ledger_invoicing.domain.pricing import CountryFields, FieldValue
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.regimes import Direction, DocumentKind, TaxCategoryKind
lazy from ledger_kernel.tenancy import Mode

type StoredValue = str | dict[str, StoredValue]
"""A country field's value as JSON keeps it: text, or an object of them."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _InvoiceRow:
    """A row of ``invoices``, as read."""

    id: UUID
    organization_id: UUID
    company_id: UUID
    mode: Mode
    kind: DocumentKind
    direction: Direction
    status: InvoiceStatus
    external_reference: str | None
    corrects: UUID | None
    currency: str
    exchange_rate: Decimal | None
    requested_issue_date: date | None
    requested_tax_point: date | None
    requested_establishment: str | None
    fiscal: dict[str, dict[str, StoredValue]]
    regime: str
    establishment_code: str
    establishment_time_zone: str
    issue_date: date
    tax_point: date
    net: Decimal
    tax: Decimal
    gross: Decimal
    functional_currency: str | None
    functional_net: Decimal | None
    functional_tax: Decimal | None
    functional_gross: Decimal | None
    buyer_legal_name: str | None
    buyer_tax_scheme: str | None
    buyer_tax_value: str | None
    buyer_address: str | None
    buyer_email: str | None
    seller_legal_name: str | None
    seller_tax_scheme: str | None
    seller_tax_value: str | None
    void_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None
    voided_at: datetime | None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _LineRow:
    """A row of ``invoice_lines``, as read."""

    invoice_id: UUID
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    gross: Decimal
    base: Decimal


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _LineTaxRow:
    """A row of ``invoice_line_taxes``, as read."""

    invoice_id: UUID
    position: int
    place: int
    category: str
    kind: TaxCategoryKind
    percent: Decimal
    since: date
    tax: Decimal


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _TaxRow:
    """A row of ``invoice_taxes``, as read."""

    invoice_id: UUID
    place: int
    category: str
    percent: Decimal
    base: Decimal
    tax: Decimal
    functional_base: Decimal | None
    functional_tax: Decimal | None


_INVOICES: Final = TypeAdapter(list[_InvoiceRow])
_LINES: Final = TypeAdapter(list[_LineRow])
_LINE_TAXES: Final = TypeAdapter(list[_LineTaxRow])
_TAXES: Final = TypeAdapter(list[_TaxRow])
_LINE_COLUMNS: Final = (
    InvoiceLineRecord.invoice_id,
    InvoiceLineRecord.position,
    InvoiceLineRecord.description,
    InvoiceLineRecord.quantity,
    InvoiceLineRecord.unit_price,
    InvoiceLineRecord.discount,
    InvoiceLineRecord.gross,
    InvoiceLineRecord.base,
)
_LINE_TAX_COLUMNS: Final = (
    InvoiceLineTaxRecord.invoice_id,
    InvoiceLineTaxRecord.position,
    InvoiceLineTaxRecord.place,
    InvoiceLineTaxRecord.category,
    InvoiceLineTaxRecord.kind,
    InvoiceLineTaxRecord.percent,
    InvoiceLineTaxRecord.since,
    InvoiceLineTaxRecord.tax,
)
_TAX_COLUMNS: Final = (
    InvoiceTaxRecord.invoice_id,
    InvoiceTaxRecord.place,
    InvoiceTaxRecord.category,
    InvoiceTaxRecord.percent,
    InvoiceTaxRecord.base,
    InvoiceTaxRecord.tax,
    InvoiceTaxRecord.functional_base,
    InvoiceTaxRecord.functional_tax,
)
_NOT_VOIDED: Final = InvoiceRecord.status != InvoiceStatus.VOIDED.value


_SELLER: Final = "seller"
"""The prefix of the seller's columns."""


class IncompleteRowError(LedgerError):
    """A row holds part of columns that go together: a defect the table's checks should have kept out."""

    code: ClassVar[str] = "invoice_row_incomplete"

    def __init__(self, columns: str) -> None:
        super().__init__(f"invoices.{columns}_* are set in part: the row is not whole")


def tax_id_of(scheme: str | None, value: str | None, *, columns: str) -> TaxId | None:
    """The tax id of the columns ``<columns>_scheme`` and ``_value``: both set, or neither (``None``)."""
    if scheme is None and value is None:
        return None
    if scheme is None or value is None:
        raise IncompleteRowError(columns)
    return TaxId(TaxIdScheme(scheme), value)


def seller_from_columns(legal_name: str | None, scheme: str | None, value: str | None) -> Seller | None:
    """The seller of the ``seller_*`` columns: its name and its tax id, or none of them (a draft)."""
    if legal_name is None and scheme is None and value is None:
        return None
    if legal_name is None or scheme is None or value is None:
        raise IncompleteRowError(_SELLER)
    return Seller(legal_name=legal_name, tax_id=TaxId(TaxIdScheme(scheme), value))


def _fields(stored: Mapping[str, Mapping[str, StoredValue]]) -> CountryFields:
    """The country fields as the terms hold them: read-only, as ``Terms`` copies them anyway."""
    return MappingProxyType(
        {namespace: MappingProxyType(dict(values)) for namespace, values in stored.items()}
    )


@final
@dataclass(frozen=True, slots=True)
class _Parts:
    """The rows of one document's lines, their taxes and its totals' categories, each in its order."""

    lines: list[_LineRow]
    line_taxes: list[_LineTaxRow]
    taxes: list[_TaxRow]


def _terms(row: _InvoiceRow, lines: Sequence[_LineRow], line_taxes: Sequence[_LineTaxRow]) -> Terms:
    taxes: dict[int, list[str]] = {}
    for tax in line_taxes:
        taxes.setdefault(tax.position, []).append(tax.category)
    buyer = None
    if row.buyer_legal_name is not None:
        buyer = Party(
            legal_name=LegalName(row.buyer_legal_name),
            tax_id=tax_id_of(row.buyer_tax_scheme, row.buyer_tax_value, columns="buyer_tax"),
            address=None if row.buyer_address is None else Address(row.buyer_address),
            email=None if row.buyer_email is None else Email(row.buyer_email),
        )
    return Terms(
        currency=Currency.of(row.currency),
        lines=tuple(
            LineTerms(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount=line.discount,
                taxes=tuple(taxes[line.position]),
            )
            for line in lines
        ),
        exchange_rate=row.exchange_rate,
        issue_date=row.requested_issue_date,
        tax_point=row.requested_tax_point,
        establishment=row.requested_establishment,
        buyer=buyer,
        fiscal=_fields(row.fiscal),
    )


def _totals(
    net: Decimal, tax: Decimal, gross: Decimal, currency: Currency, categories: tuple[CategoryTotal, ...]
) -> Totals:
    return Totals(
        net=Money(net, currency),
        categories=categories,
        tax=Money(tax, currency),
        gross=Money(gross, currency),
    )


def _functional(row: _InvoiceRow, taxes: Sequence[_TaxRow]) -> Totals | None:
    """The totals in the functional currency of a document in another one; ``None`` otherwise.

    The row's functional columns are all set or none (``ck_invoices_functional``), and so are each category's.
    """
    code, net, tax, gross = (
        row.functional_currency,
        row.functional_net,
        row.functional_tax,
        row.functional_gross,
    )
    if code is None or net is None or tax is None or gross is None:
        return None
    currency = Currency.of(code)
    categories = tuple(
        CategoryTotal(
            category=TaxCategoryKey(total.category),
            percent=total.percent,
            base=Money(total.functional_base, currency),
            tax=Money(total.functional_tax, currency),
        )
        for total in taxes
        if total.functional_base is not None and total.functional_tax is not None
    )
    return _totals(net, tax, gross, currency, categories)


def _invoice(row: _InvoiceRow, parts: _Parts) -> Invoice:
    terms = _terms(row, parts.lines, parts.line_taxes)
    currency = terms.currency
    by_line: dict[int, list[LineTax]] = {}
    for tax in parts.line_taxes:
        by_line.setdefault(tax.position, []).append(
            LineTax(
                category=TaxCategoryKey(tax.category),
                kind=tax.kind,
                percent=tax.percent,
                since=tax.since,
                tax=Money(tax.tax, currency),
            )
        )
    priced = tuple(
        PricedLine(
            terms=written,
            gross=Money(line.gross, currency),
            discount=Money(written.discount, currency),
            base=Money(line.base, currency),
            taxes=tuple(by_line[line.position]),
        )
        for written, line in zip(terms.lines, parts.lines, strict=True)
    )
    categories = tuple(
        CategoryTotal(
            category=TaxCategoryKey(tax.category),
            percent=tax.percent,
            base=Money(tax.base, currency),
            tax=Money(tax.tax, currency),
        )
        for tax in parts.taxes
    )
    regime = RegimeKey(row.regime)
    return Invoice(
        id=InvoiceId(row.id),
        tenant=Tenant(OrganizationId(row.organization_id), CompanyId(row.company_id), row.mode),
        kind=row.kind,
        direction=row.direction,
        status=row.status,
        external_reference=(
            None if row.external_reference is None else ExternalReference(row.external_reference)
        ),
        corrects=None if row.corrects is None else InvoiceId(row.corrects),
        terms=terms,
        pricing=Pricing(
            regime=regime,
            establishment=EstablishmentRef(
                code=row.establishment_code, time_zone=row.establishment_time_zone
            ),
            issue_date=row.issue_date,
            tax_point=row.tax_point,
            lines=priced,
            totals=_totals(row.net, row.tax, row.gross, currency, categories),
            functional=_functional(row, parts.taxes),
            fiscal=terms.fiscal.get(regime, MappingProxyType({})),
        ),
        seller=seller_from_columns(row.seller_legal_name, row.seller_tax_scheme, row.seller_tax_value),
        void_reason=None if row.void_reason is None else Reason(row.void_reason),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        issued_at=row.issued_at,
        voided_at=row.voided_at,
    )


def _plain(value: FieldValue) -> StoredValue:
    """A country field's value as JSON keeps it: the terms hold read-only mappings, JSON plain objects."""
    if isinstance(value, str):
        return value
    return {key: _plain(item) for key, item in value.items()}


def _changed(invoice: Invoice) -> dict[str, object]:
    """What a change of ``invoice`` may write of its row: everything but its identity and its tenant."""
    terms, pricing, totals = invoice.terms, invoice.pricing, invoice.pricing.totals
    functional, buyer, seller = pricing.functional, terms.buyer, invoice.seller
    buyer_tax_id = None if buyer is None else buyer.tax_id
    return {
        "status": invoice.status.value,
        "currency": terms.currency.code,
        "exchange_rate": terms.exchange_rate,
        "requested_issue_date": terms.issue_date,
        "requested_tax_point": terms.tax_point,
        "requested_establishment": terms.establishment,
        "fiscal": {namespace: _plain(values) for namespace, values in terms.fiscal.items()},
        "regime": pricing.regime,
        "establishment_code": pricing.establishment.code,
        "establishment_time_zone": pricing.establishment.time_zone,
        "issue_date": pricing.issue_date,
        "tax_point": pricing.tax_point,
        "net": totals.net.amount,
        "tax": totals.tax.amount,
        "gross": totals.gross.amount,
        "functional_currency": None if functional is None else functional.net.currency.code,
        "functional_net": None if functional is None else functional.net.amount,
        "functional_tax": None if functional is None else functional.tax.amount,
        "functional_gross": None if functional is None else functional.gross.amount,
        "buyer_legal_name": None if buyer is None else buyer.legal_name,
        "buyer_tax_scheme": None if buyer_tax_id is None else buyer_tax_id.scheme,
        "buyer_tax_value": None if buyer_tax_id is None else buyer_tax_id.value,
        "buyer_address": None if buyer is None else buyer.address,
        "buyer_email": None if buyer is None else buyer.email,
        "seller_legal_name": None if seller is None else seller.legal_name,
        "seller_tax_scheme": None if seller is None else seller.tax_id.scheme,
        "seller_tax_value": None if seller is None else seller.tax_id.value,
        "void_reason": invoice.void_reason,
        "version": invoice.version,
        "updated_at": invoice.updated_at,
        "issued_at": invoice.issued_at,
        "voided_at": invoice.voided_at,
    }


@final
class PgInvoices(Invoices):
    """The documents of the unit's tenant (a company in a mode), in PostgreSQL."""

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        """The documents of ``tenant``, whose mode is ``mode`` (a unit of no mode reads no document)."""
        self._connection = connection
        self._tenant = tenant
        self._mode = mode.value

    @override
    async def add(self, invoice: Invoice) -> InvoiceId | None:
        self._require_own(invoice)
        tenant = self._identity(invoice)
        added = await self._connection.execute(
            insert(InvoiceRecord)
            .values(
                id=invoice.id,
                **tenant,
                kind=invoice.kind.value,
                direction=invoice.direction.value,
                external_reference=invoice.external_reference,
                corrects=invoice.corrects,
                created_at=invoice.created_at,
                **_changed(invoice),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    InvoiceRecord.organization_id,
                    InvoiceRecord.company_id,
                    InvoiceRecord.mode,
                    InvoiceRecord.kind,
                    InvoiceRecord.external_reference,
                ],
                index_where=InvoiceRecord.external_reference.is_not(None),
            )
            .returning(InvoiceRecord.id)
        )
        if added.first() is None:  # the business key's holder, which the unique index found
            holder = await self._connection.execute(
                select(InvoiceRecord.id).where(
                    *self._mine(),
                    InvoiceRecord.kind == invoice.kind.value,
                    InvoiceRecord.external_reference == invoice.external_reference,
                )
            )
            return InvoiceId(holder.scalar_one())
        await self._add_parts(invoice)
        return None

    @override
    async def get(self, invoice: InvoiceId) -> Invoice | None:
        found = await self._read(self._one(invoice))
        return found[0] if found else None

    @override
    async def locked(self, invoice: InvoiceId) -> Invoice | None:
        found = await self._read(self._one(invoice).with_for_update())
        return found[0] if found else None

    @override
    async def update(self, invoice: Invoice) -> None:
        self._require_own(invoice)
        changed = await self._connection.execute(
            update(InvoiceRecord)
            .where(
                *self._mine(), InvoiceRecord.id == invoice.id, InvoiceRecord.version == invoice.version - 1
            )
            .values(**_changed(invoice))
        )
        if changed.rowcount != 1:
            raise StaleWriteError
        for table in (
            InvoiceLineTaxRecord,
            InvoiceTaxRecord,
            InvoiceLineRecord,
        ):  # what refers to a line first
            await self._connection.execute(delete(table).where(*self._parts_of(table, [invoice.id])))
        await self._add_parts(invoice)

    @override
    async def corrected_by(self, invoice: InvoiceId) -> bool:
        noted = exists().where(*self._mine(), InvoiceRecord.corrects == invoice, _NOT_VOIDED)
        return bool((await self._connection.execute(select(noted))).scalar_one())

    @override
    async def page(self, request: PageRequest, filters: InvoiceFilters) -> Page[Invoice]:
        conditions: list[ColumnElement[bool]] = []
        if filters.kind is not None:
            conditions.append(InvoiceRecord.kind == filters.kind.value)
        if filters.direction is not None:
            conditions.append(InvoiceRecord.direction == filters.direction.value)
        if filters.status is not None:
            conditions.append(InvoiceRecord.status == filters.status.value)
        if filters.external_reference is not None:
            conditions.append(InvoiceRecord.external_reference == filters.external_reference)
        statement = select(InvoiceRecord).where(*self._mine(), *conditions)
        found = await self._read(paged(statement, InvoiceRecord.id, request))
        return page_of(found, request, key=lambda invoice: invoice.id)

    def _require_own(self, invoice: Invoice) -> None:
        """Refuse another tenant's document."""
        if invoice.tenant != self._tenant:
            raise ForeignTenantError

    def _identity(self, invoice: Invoice) -> dict[str, object]:
        return {
            "organization_id": invoice.tenant.organization,
            "company_id": invoice.tenant.company,
            "mode": self._mode,
        }

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        return (
            InvoiceRecord.organization_id == self._tenant.organization,
            InvoiceRecord.company_id == self._tenant.company,
            InvoiceRecord.mode == self._mode,
        )

    def _one(self, invoice: InvoiceId) -> Select[InvoiceRecord]:
        return select(InvoiceRecord).where(*self._mine(), InvoiceRecord.id == invoice)

    async def _read(self, statement: Select[InvoiceRecord]) -> list[Invoice]:
        """The documents ``statement`` selects, in its order, each with its lines, taxes and totals."""
        found = _INVOICES.validate_python(await self._mapped(statement))
        if not found:
            return []
        ids = [row.id for row in found]
        parts = {identifier: _Parts([], [], []) for identifier in ids}
        lines = select(*_LINE_COLUMNS).where(*self._parts_of(InvoiceLineRecord, ids))
        in_order = lines.order_by(InvoiceLineRecord.invoice_id, InvoiceLineRecord.position)
        for line in _LINES.validate_python(await self._mapped(in_order)):
            parts[line.invoice_id].lines.append(line)
        line_taxes = select(*_LINE_TAX_COLUMNS).where(*self._parts_of(InvoiceLineTaxRecord, ids))
        line_taxes_in_order = line_taxes.order_by(
            InvoiceLineTaxRecord.invoice_id, InvoiceLineTaxRecord.position, InvoiceLineTaxRecord.place
        )
        for tax in _LINE_TAXES.validate_python(await self._mapped(line_taxes_in_order)):
            parts[tax.invoice_id].line_taxes.append(tax)
        taxes = select(*_TAX_COLUMNS).where(*self._parts_of(InvoiceTaxRecord, ids))
        taxes_in_order = taxes.order_by(InvoiceTaxRecord.invoice_id, InvoiceTaxRecord.place)
        for total in _TAXES.validate_python(await self._mapped(taxes_in_order)):
            parts[total.invoice_id].taxes.append(total)
        return [_invoice(row, parts[row.id]) for row in found]

    async def _mapped[*Columns](self, statement: Select[*Columns]) -> list[dict[str, object]]:
        """The rows ``statement`` selects, each as its columns by name."""
        return [dict(row) for row in (await self._connection.execute(statement)).mappings()]

    def _parts_of(
        self, table: type[InvoiceLineRecord | InvoiceLineTaxRecord | InvoiceTaxRecord], ids: Sequence[UUID]
    ) -> tuple[ColumnElement[bool], ...]:
        """The rows of ``table`` that belong to the documents ``ids``, of the unit's tenant."""
        return (
            table.organization_id == self._tenant.organization,
            table.company_id == self._tenant.company,
            table.mode == self._mode,
            table.invoice_id.in_(ids),
        )

    async def _add_parts(self, invoice: Invoice) -> None:
        """Keep the lines of ``invoice``, the taxes each is charged, and its totals' categories."""
        tenant = self._identity(invoice)
        pricing = invoice.pricing
        lines = [
            {
                **tenant,
                "invoice_id": invoice.id,
                "position": position,
                "description": line.terms.description,
                "quantity": line.terms.quantity,
                "unit_price": line.terms.unit_price,
                "discount": line.terms.discount,
                "gross": line.gross.amount,
                "base": line.base.amount,
            }
            for position, line in enumerate(pricing.lines)
        ]
        line_taxes = [
            {
                **tenant,
                "invoice_id": invoice.id,
                "position": position,
                "place": place,
                "category": tax.category,
                "kind": tax.kind.value,
                "percent": tax.percent,
                "since": tax.since,
                "tax": tax.tax.amount,
            }
            for position, line in enumerate(pricing.lines)
            for place, tax in enumerate(line.taxes)
        ]
        functional = None if pricing.functional is None else pricing.functional.categories
        taxes = [
            {
                **tenant,
                "invoice_id": invoice.id,
                "place": place,
                "category": total.category,
                "percent": total.percent,
                "base": total.base.amount,
                "tax": total.tax.amount,
                "functional_base": None if functional is None else functional[place].base.amount,
                "functional_tax": None if functional is None else functional[place].tax.amount,
            }
            for place, total in enumerate(pricing.totals.categories)
        ]
        await self._connection.execute(insert(InvoiceLineRecord), lines)
        await self._connection.execute(insert(InvoiceLineTaxRecord), line_taxes)
        await self._connection.execute(insert(InvoiceTaxRecord), taxes)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _MessageRow:
    """An authority's message, as ``fiscal_statuses.messages`` keeps it."""

    code: str
    message: str
    lang: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _StatusRow:
    """A row of ``fiscal_statuses``, as read."""

    invoice_id: UUID
    channel: str
    environment: str
    status: FiscalStatusName
    version: int
    authority_number: str | None
    messages: list[_MessageRow]
    regime: str
    details: dict[str, Detail]
    updated_at: datetime


_STATUSES: Final = TypeAdapter(list[_StatusRow])
_ACCEPTANCES: Final = tuple(status.value for status in ACCEPTANCES)
_STATUS_COLUMNS: Final = (
    FiscalStatusRecord.invoice_id,
    FiscalStatusRecord.channel,
    FiscalStatusRecord.environment,
    FiscalStatusRecord.status,
    FiscalStatusRecord.version,
    FiscalStatusRecord.authority_number,
    FiscalStatusRecord.messages,
    FiscalStatusRecord.regime,
    FiscalStatusRecord.details,
    FiscalStatusRecord.updated_at,
)


def _status(row: _StatusRow) -> FiscalStatus:
    return FiscalStatus(
        channel=row.channel,
        environment=row.environment,
        status=row.status,
        version=row.version,
        authority_number=row.authority_number,
        messages=tuple(
            AuthorityMessage(code=message.code, message=message.message, lang=message.lang)
            for message in row.messages
        ),
        regime=row.regime,
        details=row.details,
        updated_at=row.updated_at,
    )


@final
class PgFiscalMirror(FiscalMirror):
    """The fiscal mirror of the unit's tenant (a company in a mode), in PostgreSQL.

    A status is written by one statement, ``INSERT ... ON CONFLICT (tenant, invoice, channel) DO UPDATE ...
    WHERE`` the kept version is older, which answers the row only when it wrote it. It first takes a share of
    the document's row lock (``FOR SHARE``), which also tells a document the tenant does not have.
    """

    __slots__ = ("_connection", "_mode", "_tenant")

    def __init__(self, connection: AsyncConnection, tenant: Tenant, mode: Mode) -> None:
        self._connection = connection
        self._tenant = tenant
        self._mode = mode.value

    @override
    async def of(self, invoices: Sequence[InvoiceId]) -> Mapping[InvoiceId, tuple[FiscalStatus, ...]]:
        if not invoices:
            return {}
        found: dict[InvoiceId, list[FiscalStatus]] = {invoice: [] for invoice in invoices}
        statement = (
            select(*_STATUS_COLUMNS)
            .where(*self._mine(), FiscalStatusRecord.invoice_id.in_(list(invoices)))
            .order_by(FiscalStatusRecord.invoice_id, FiscalStatusRecord.channel)
        )
        rows = (await self._connection.execute(statement)).mappings()
        for row in _STATUSES.validate_python([dict(row) for row in rows]):
            found[InvoiceId(row.invoice_id)].append(_status(row))
        return {invoice: tuple(statuses) for invoice, statuses in found.items()}

    @override
    async def accepted(self, invoice: InvoiceId) -> bool:
        statement = select(
            exists().where(
                *self._mine(),
                FiscalStatusRecord.invoice_id == invoice,
                FiscalStatusRecord.status.in_(_ACCEPTANCES),
            )
        )
        return bool((await self._connection.execute(statement)).scalar_one())

    @override
    async def apply(self, invoice: InvoiceId, status: FiscalStatus) -> Applied | None:
        shared = (
            select(InvoiceRecord.id)
            .where(
                InvoiceRecord.organization_id == self._tenant.organization,
                InvoiceRecord.company_id == self._tenant.company,
                InvoiceRecord.mode == self._mode,
                InvoiceRecord.id == invoice,
            )
            .with_for_update(read=True)
        )
        if (await self._connection.execute(shared)).first() is None:
            return None
        values: dict[str, object] = {
            "environment": status.environment,
            "status": status.status.value,
            "version": status.version,
            "authority_number": status.authority_number,
            "messages": [
                {"code": message.code, "message": message.message, "lang": message.lang}
                for message in status.messages
            ],
            "regime": status.regime,
            "details": dict(status.details),
            "updated_at": status.updated_at,
        }
        fresh = insert(FiscalStatusRecord).values(
            organization_id=self._tenant.organization,
            company_id=self._tenant.company,
            mode=self._mode,
            invoice_id=invoice,
            channel=status.channel,
            **values,
        )
        written = fresh.on_conflict_do_update(
            index_elements=[
                FiscalStatusRecord.organization_id,
                FiscalStatusRecord.company_id,
                FiscalStatusRecord.mode,
                FiscalStatusRecord.invoice_id,
                FiscalStatusRecord.channel,
            ],
            set_={name: fresh.excluded[name] for name in values},
            where=FiscalStatusRecord.version < fresh.excluded.version,
        ).returning(FiscalStatusRecord.version)
        kept = (await self._connection.execute(written)).first()
        return Applied.STALE if kept is None else Applied.APPLIED

    def _mine(self) -> tuple[ColumnElement[bool], ...]:
        return (
            FiscalStatusRecord.organization_id == self._tenant.organization,
            FiscalStatusRecord.company_id == self._tenant.company,
            FiscalStatusRecord.mode == self._mode,
        )
