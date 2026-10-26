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
``*Info``, as identity's are; ``*View`` names the HTTP models (so the spec's ``FiscalStatusView`` is
``FiscalStatusInfo`` here).

**The fiscal mirror.** ``apply_fiscal_status`` keeps what a fiscal channel last reported of a document
(``fiscal``'s consumer and its synchronous path call it with a ``SystemCall``): a newer version per document
and channel wins and is announced (``invoicing.invoice.fiscal_updated``); an older or equal one is ``STALE``
and changes nothing, so a redelivery is harmless (rule 4).

**Extension points** (ADR 0011; invoicing imports no channel and no kit type, so its words are its own).
Both are optional chains, read at call time, in ``(order, module)`` order:

- ``IssuePolicy``: ``check`` runs on the document as it will be issued (its id minted, its seller set, its
  times the clock's: the unit's replace them), with no unit open; the objections of every policy are
  concatenated in chain order and any refuses it (422 ``invalid_document``, nothing stored). ``check`` is
  not time-bounded by invoicing: it runs before anything is stored, so a slow one delays the answer and
  never leaves a document half done; an implementation keeps it to local reads (fiscal: enrollments and the
  channels' own checks, no authority call). With a chain, the
  keys of ``fiscal.<regime>`` the regime does not declare are the chain's to judge (Decision 24); without one
  they are refused. Once the document is issued and committed, each ``after_issue`` runs in chain order with
  what is left of ``ISSUE_BUDGET``, cancelled when that runs out; whatever it raises is logged by its type and
  passed over (fiscal's recovery consumer finishes the work, §8.5). An empty chain issues with ``fiscal: []``.
- ``Renderer``: the first that answers a document's representation for a channel renders it; none is 404
  ``representation_unavailable``.

An implementation is bound for other modules to call, so each of its coroutines is a ``@facade``, and is
trusted with the call it gets: a route of its own that shows a document asks ``invoicing.invoices.read``.
"""

import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final, Protocol, TypeIs, final, override, runtime_checkable
lazy from collections.abc import Mapping, Sequence
lazy from datetime import date, datetime
lazy from decimal import Decimal

from ledger_invoicing.domain.errors import InvoiceNotFoundError
from ledger_invoicing.domain.fiscal import Applied, FiscalStatusName
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_invoicing.domain.objections import Objection
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Mode
from ledger_platform.runtime.keys import Key
lazy from ledger_invoicing.domain.pricing import CountryFields, FieldValue
lazy from ledger_kernel.addresses import FiscalAddress
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
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.runtime.calls import AnyCall, Call, SystemCall

__all__ = [
    "INVOICING_API",
    "ISSUE_BUDGET",
    "ISSUE_POLICY",
    "MAX_RENDERED",
    "PDF",
    "RENDERER",
    "Applied",
    "AuthorityMessageInfo",
    "CountryFields",
    "EstablishmentOfIssue",
    "FieldValue",
    "FiscalStatusInfo",
    "FiscalStatusName",
    "InvoiceId",
    "InvoiceInfo",
    "InvoiceNotFoundError",
    "InvoiceStatus",
    "InvoicingApi",
    "IssuePolicy",
    "LineInfo",
    "LineTaxInfo",
    "Objection",
    "PartyInfo",
    "RenderedDocument",
    "Renderer",
    "SellerInfo",
]

ISSUE_BUDGET: Final = timedelta(milliseconds=1500)
"""What every ``after_issue`` of the chain may take together, once a document is issued (§12, §8.5 step 4)."""
PDF: Final = "application/pdf"
"""The one representation invoicing answers (what DGII's printed form is): the route's OpenAPI says so."""
MAX_RENDERED: Final = 10 * 1024 * 1024
"""The largest representation answered: 10 MiB."""
_FILENAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
"""A file name a ``Content-Disposition`` header carries as it is: no quote, space or slash, no dot first."""


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
    """The company that issued the document, as it was then: its legal name, primary tax id and address."""

    legal_name: str
    tax_id: TaxId
    address: FiscalAddress | None
    """Its fiscal address as identity had it when the document was issued; ``None`` if it stated none."""

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


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMessageInfo:
    """One of the authority's messages, verbatim: never translated, never logged (§7.10)."""

    code: str
    message: str = field(repr=False)
    lang: str


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class FiscalStatusInfo:
    """What a fiscal channel reports of a document (the spec's ``FiscalStatusView``), without its version.

    The kit's bounds hold (fiscal maps a ``StatusUpdate`` onto it): at most 20 messages, each code 64 and text
    500 characters at most, an authority number of 64 at most. ``details`` are the update's under its
    ``regime`` (``update.details["do"]``), shown on the wire as ``fiscal[].do`` and never read by invoicing.
    """

    channel: str
    environment: str
    status: FiscalStatusName
    authority_number: str | None
    messages: tuple[AuthorityMessageInfo, ...]
    regime: str
    details: Mapping[str, JsonValue] = field(hash=False)
    updated_at: datetime
    """When the channel's document reached this status (the update's ``occurred_at``)."""

    @override
    def __repr__(self) -> str:
        return f"FiscalStatusInfo(channel={self.channel!r}, status={self.status.value!r})"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedDocument:
    """A document's printed representation, as a ``Renderer`` answers it: answered inline as ``filename``."""

    media_type: str
    content: bytes = field(repr=False)
    filename: str

    def __post_init__(self) -> None:
        if self.media_type != PDF:
            raise InvalidValueError(field="rendered_document.media_type", requirement=PDF)
        if len(self.content) > MAX_RENDERED:
            raise InvalidValueError(
                field="rendered_document.content", requirement=f"at most {MAX_RENDERED} bytes"
            )
        if _FILENAME.fullmatch(self.filename) is None:
            raise InvalidValueError(
                field="rendered_document.filename",
                requirement="1 to 128 letters, digits, dots, dashes and underscores, not led by a dot",
            )


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

    @abstractmethod
    async def apply_fiscal_status(
        self, call: SystemCall, document: InvoiceId, status: FiscalStatusInfo, version: int
    ) -> Applied:
        """Keep ``status`` at ``version`` in ``document``'s fiscal mirror, if newer than the one kept.

        ``APPLIED`` (kept and announced) when the channel has no status kept at ``version`` or later, else
        ``STALE`` (nothing changes); ``DOCUMENT_VOIDED`` (nothing kept) when the document is voided: the
        channel must not number it, so fiscal applies ``pending`` before it numbers a document. One unit of
        its own, which takes a share of the document's lock first.
        ``InvoiceNotFoundError`` when the call's tenant has no such document; ``InvalidValueError`` for a
        version below 1, a status out of its bounds, or a call whose scope is not a company in a mode.
        """


@runtime_checkable
class IssuePolicy(Protocol, metaclass=ABCMeta):
    """An extension point: what a module says of a document invoicing issues (see the module)."""

    @abstractmethod
    async def check(self, call: Call, document: InvoiceInfo) -> Sequence[Objection]:
        """Why ``document`` (as it will be issued) must not be issued; empty when it may. No unit is open."""

    @abstractmethod
    async def after_issue(self, call: Call, document: InvoiceInfo, *, budget: timedelta) -> None:
        """What to do once ``document`` is issued and committed, within ``budget``. No unit is open."""


@runtime_checkable
class Renderer(Protocol, metaclass=ABCMeta):
    """An extension point: a document's printed representation for a channel (Decision 22)."""

    @abstractmethod
    async def render(self, call: Call, document: InvoiceInfo, channel: str) -> RenderedDocument | None:
        """``document``'s representation for ``channel``; ``None`` when this module renders none for it."""


def _is_invoicing_api(value: object) -> TypeIs[InvoicingApi]:
    return isinstance(value, InvoicingApi)


def _is_issue_policy(value: object) -> TypeIs[IssuePolicy]:
    return isinstance(value, IssuePolicy)


def _is_renderer(value: object) -> TypeIs[Renderer]:
    return isinstance(value, Renderer)


INVOICING_API: Final = Key("invoicing", _is_invoicing_api)
"""invoicing's API under its name: how a module reads it and how invoicing binds it."""
ISSUE_POLICY: Final = Key("invoicing.IssuePolicy", _is_issue_policy)
"""The ``IssuePolicy`` point: how a module binds its implementation and invoicing reads the chain."""
RENDERER: Final = Key("invoicing.Renderer", _is_renderer)
"""The ``Renderer`` point."""
