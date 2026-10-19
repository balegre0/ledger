"""invoicing's API for the other modules (§3.4): Protocols, frozen DTOs and the errors that cross it.

Callers receive an ``InvoicingApi`` from the composition root (``apis.get(INVOICING_API)``, once their
manifest declares the dependency) and never see a domain object or a row. Every method opens a unit of its own
and none may be called while the caller holds one (``UnitOpenError``).

A call names the tenant it reads: the company and the mode of its scope (a request's ``Call`` as its edge
authorized it, or a ``SystemCall`` with the scope of the event or job that caused it). A call whose scope is
not a company in a mode is a defect of its caller (``InvalidValueError``); another tenant's document is as
missing as one never made (``InvoiceNotFoundError``). Who may read a company's documents is the caller's
edge's to judge (the HTTP edge asks identity for ``invoicing.invoices.read``): the API trusts the call it is
given, as identity's does.

An ``InvoiceInfo`` is a document as it is kept: tax ids are qualified and in clear (a module inside the
tenant's boundary needs them, fiscal to sign), so its ``repr`` (and so any log line, span or error that shows
one) names its id, kind, status and version only, and a party's ``repr`` its tax id masked. DTOs are named
``*Info``, as identity's are; ``*View`` names the HTTP models. ``apply_fiscal_status`` and the
``IssuePolicy`` chain arrive with the ``fiscal`` module (M3b, Decision 13).
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Final, Protocol, TypeIs, final, override, runtime_checkable
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_invoicing.domain.errors import InvoiceNotFoundError
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_kernel.tenancy import Mode
from ledger_platform.runtime.keys import Key
lazy from ledger_invoicing.domain.pricing import CountryFields, FieldValue
lazy from ledger_kernel.money import Currency, Money
lazy from ledger_kernel.regimes import (
    Direction,
    DocumentKind,
    RegimeKey,
    TaxCategoryKey,
    TaxCategoryKind,
    Totals,
)
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import AnyCall

__all__ = [
    "INVOICING_API",
    "CountryFields",
    "EstablishmentOfIssue",
    "FieldValue",
    "InvoiceId",
    "InvoiceInfo",
    "InvoiceNotFoundError",
    "InvoiceStatus",
    "InvoicingApi",
    "LineInfo",
    "LineTaxInfo",
    "PartyInfo",
    "SellerInfo",
]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EstablishmentOfIssue:
    """Where a document was issued from: its establishment's code, and the IANA zone of its dates."""

    code: str
    time_zone: str
    """The zone its issue date and tax point are days of (rule 12, §7.1)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class SellerInfo:
    """The company that issued the document, as it was then: its legal name and primary tax id."""

    legal_name: str
    tax_id: TaxId

    @override
    def __repr__(self) -> str:
        return f"SellerInfo(tax_id={self.tax_id!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class PartyInfo:
    """The other party of a document, as the client sent it: a sale's buyer, a purchase's supplier."""

    legal_name: str
    tax_id: TaxId | None
    address: str | None
    email: str | None

    @override
    def __repr__(self) -> str:
        return f"PartyInfo(tax_id={self.tax_id!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LineTaxInfo:
    """A tax a line is charged: the rate resolved at the tax point, and what it comes to on the line alone.

    ``amount`` is what the regime's calculator computes for a document of this line only: rounded where the
    regime rounds (per category, per line or per document), else exact.

    ``category`` and ``since`` name the rate in the regime's timeline; the document's tax per category is in
    its ``totals`` (a regime that rounds per category does not split it back into lines).
    """

    category: TaxCategoryKey
    kind: TaxCategoryKind
    percent: Decimal
    since: date
    amount: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class LineInfo:
    """A line as the client wrote it and as it was priced, in the document's currency."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    """An amount, never a percentage."""
    gross: Money
    """``quantity x unit_price``, exact."""
    base: Money
    """``gross - discount``, rounded half up to the currency's minor unit: what the line is taxed on."""
    taxes: tuple[LineTaxInfo, ...]
    """In the order the line names them."""

    @override
    def __repr__(self) -> str:
        return f"LineInfo(taxes={tuple(tax.category for tax in self.taxes)!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class InvoiceInfo:
    """A commercial document at a version (its ``ETag``): an invoice, a credit or a debit note.

    Everything it charges was resolved once, when it was priced: its regime, its establishment and the day it
    keeps, its lines' rates at the tax point, its totals (and in the company's functional currency, for a
    document in another one). An issued document names its seller; a draft does not yet.
    """

    id: InvoiceId
    tenant: Tenant
    """Its organization, company and mode (always set: a document exists in one mode); the scope its events
    carry, and the one a call about it names."""
    kind: DocumentKind
    """The wire's ``type``: an invoice, a credit note or a debit note."""
    direction: Direction
    status: InvoiceStatus
    version: int
    external_reference: str | None
    """Its business key, unique per company, mode and type for ever."""
    corrects: InvoiceId | None
    """The invoice a credit or debit note corrects; ``None`` for an invoice."""
    regime: RegimeKey
    """The regime that priced it: the one that governs the company's jurisdiction."""
    establishment: EstablishmentOfIssue
    currency: Currency
    exchange_rate: Decimal | None
    """Units of the functional currency per one of ``currency``; ``None`` in the functional currency."""
    issue_date: date
    tax_point: date
    seller: SellerInfo | None
    """Set once it is issued."""
    buyer: PartyInfo | None
    lines: tuple[LineInfo, ...]
    totals: Totals
    """In ``currency``."""
    functional_totals: Totals | None
    """In the company's functional currency, for a document in another one; ``None`` otherwise."""
    fields: CountryFields = field(hash=False)
    """The country fields it carries, under their regime's namespace (``{"do": {...}}``, §9.8); empty when
    it was sent none."""
    created_at: datetime
    updated_at: datetime
    issued_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None

    @property
    def livemode(self) -> bool:
        """Whether it is a live document (§9.3)."""
        return self.tenant.mode is Mode.LIVE

    @override
    def __repr__(self) -> str:
        shown = f"kind={self.kind.value!r}, status={self.status.value!r}, version={self.version}"
        return f"InvoiceInfo(id={self.id!r}, {shown})"


@runtime_checkable
class InvoicingApi(Protocol, metaclass=ABCMeta):
    """What the other modules may ask of invoicing.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def document(self, call: AnyCall, invoice: InvoiceId) -> InvoiceInfo:
        """The document ``invoice`` of the call's tenant, at its current version.

        ``InvoiceNotFoundError`` when the tenant has none by that id; ``InvalidValueError`` for a call whose
        scope is not a company in a mode.
        """


def _is_invoicing_api(value: object) -> TypeIs[InvoicingApi]:
    return isinstance(value, InvoicingApi)


INVOICING_API: Final = Key("invoicing", _is_invoicing_api)
"""invoicing's API under its name: how a module reads it and how invoicing binds it."""
