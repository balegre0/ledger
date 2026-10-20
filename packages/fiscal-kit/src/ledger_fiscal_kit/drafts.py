"""What fiscal hands a channel (spec §8.4 ``drafts``): a commercial document as a fiscal document's input.

``fiscal`` builds a ``FiscalDraft`` from invoicing's document at a version (its seller and buyer as they
were then, its establishment and the zone its dates are days of, its lines with the rates resolved at the tax
point, the kernel calculator's totals) for one channel, environment and mode; the channel checks it, numbers
it and builds its authority's format from it. Nothing here reads or writes anything.

A draft is built by our own code from a document invoicing already validated, so a draft that breaks a rule
here is a defect of that code (``FiscalDraftInvalidError``, internal), never a client's input: what a channel
objects to in a valid draft is an ``Objection`` (``channels``). Parties, lines and fields are data of the
tenant and of persons: a draft's ``repr`` names its document, version, channel and kind only, a party's its
tax id masked, a line's no description.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, NewType, final, override
from uuid import UUID
lazy from collections.abc import Mapping
lazy from datetime import date
lazy from decimal import Decimal

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
lazy from ledger_kernel.ids import CompanyId, OrganizationId
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
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.data.codec import JsonValue

DocumentRef = NewType("DocumentRef", UUID)
"""A commercial document's id (invoicing's ``InvoiceId``, unwrapped): the kit never imports a module."""


class FiscalDraftInvalidError(InvalidValueError):
    """A draft that could be no fiscal document's input: a defect of the code that built it."""

    code: ClassVar[str] = "fiscal_draft_invalid"

    def __init__(self, *, field: str, requirement: str) -> None:
        super().__init__(field=f"fiscal_draft.{field}", requirement=requirement)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalParty:
    """A party as the document names it: the seller (with its primary tax id) or the buyer (as sent)."""

    tax_id: TaxId | None
    """Shown masked (``TaxId``'s ``repr``)."""
    legal_name: str | None = field(repr=False)
    address: str | None = field(default=None, repr=False)
    email: str | None = field(default=None, repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalLineTax:
    """A tax a line is charged: the rate resolved at the tax point (``category`` and ``since`` name it)."""

    category: TaxCategoryKey
    kind: TaxCategoryKind
    """The category's generic kind (standard, exempt...), for formats that speak generically."""
    percent: Decimal
    since: date
    """The day the rate took effect, in the regime's timeline."""
    amount: Money
    """What the regime's calculator computes for this line alone (invoicing's ``LineTaxInfo.amount``)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalLine:
    """A line as the client wrote it and as invoicing priced it, in the document's currency."""

    number: int
    """From 1, in the document's order."""
    description: str = field(repr=False)
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    """An amount, never a percentage."""
    base: Money
    """What the line is taxed on: ``quantity x unit_price - discount``, rounded to the currency."""
    taxes: tuple[FiscalLineTax, ...]
    goods: bool | None = None
    """Goods or a service; ``None``: the channel's default."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalReference:
    """A document this one refers to: the invoice a credit or debit note corrects."""

    document: DocumentRef
    issue_date: date


def _numbered(lines: tuple[FiscalLine, ...]) -> bool:
    return [line.number for line in lines] == list(range(1, len(lines) + 1))


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class FiscalDraft:
    """A commercial document at ``version``, for ``channel`` in ``environment`` and ``mode`` (see above)."""

    document: DocumentRef
    version: int
    """The commercial document's version the draft was built from (its ``ETag``)."""
    organization: OrganizationId
    company: CompanyId
    mode: Mode
    channel: str
    environment: str
    regime: RegimeKey
    kind: DocumentKind
    direction: Direction
    seller: FiscalParty
    """The issuing company as it was when the document was issued; its tax id is required."""
    establishment_code: str
    time_zone: str
    """The IANA zone the issue date and the tax point are days of (rule 12, §7.1)."""
    buyer: FiscalParty
    lines: tuple[FiscalLine, ...]
    """One or more, numbered from 1 in order."""
    totals: Totals
    """The kernel calculator's, in ``currency``."""
    currency: Currency
    exchange_rate: Decimal | None
    """Units of the functional currency per one of ``currency``; given with ``functional_totals`` only."""
    functional_totals: Totals | None
    references: tuple[FiscalReference, ...] = ()
    fields: Mapping[str, JsonValue] = field(hash=False)
    """This regime's country fields only (``fiscal.<regime>``, one level down); a read-only copy."""
    issue_date: date
    tax_point: date
    external_reference: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise FiscalDraftInvalidError(field="version", requirement="1 or more")
        if self.seller.tax_id is None:
            raise FiscalDraftInvalidError(field="seller.tax_id", requirement="the seller's primary tax id")
        if not self.lines:
            raise FiscalDraftInvalidError(field="lines", requirement="one line or more")
        if not _numbered(self.lines):
            raise FiscalDraftInvalidError(field="lines", requirement="numbered 1 to n in order")
        if self.totals.gross.currency != self.currency:
            raise FiscalDraftInvalidError(field="totals", requirement="in the document's currency")
        if (self.exchange_rate is None) != (self.functional_totals is None):
            raise FiscalDraftInvalidError(
                field="exchange_rate", requirement="given with the functional totals, and only with them"
            )
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    @property
    def scope(self) -> Tenant:
        """The tenant the document belongs to: the scope of every unit and call about it."""
        return Tenant(self.organization, self.company, self.mode)

    @override
    def __repr__(self) -> str:
        shown = f"version={self.version}, channel={self.channel!r}, kind={self.kind.value!r}"
        return f"FiscalDraft(document={self.document!r}, {shown})"
