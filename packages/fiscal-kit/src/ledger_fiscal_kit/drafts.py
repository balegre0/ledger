"""What fiscal hands a channel (spec §8.4 ``drafts``): a commercial document as a fiscal document's input.

``fiscal`` builds a ``FiscalDraft`` from invoicing's document at a version (its issuer and counterparty as
they were then, its establishment and the zone its dates are days of, its lines with the rates resolved at the
tax point, the kernel calculator's totals) for one channel, environment and mode; the channel checks it,
numbers it and builds its authority's format from it. Nothing here reads or writes anything.

The parties follow invoicing's ``InvoiceInfo``: ``issuer`` is its ``seller``, the company that issues the
document whatever its direction; ``counterparty`` is its ``buyer``, the other party as the client sent it (a
sale's buyer; for a self-billed purchase, DGII's types 41, 43 and 47, the supplier the company buys from), or
``None`` when the document names none. A channel maps them onto its format's blocks (DO: the issuer is always
``Emisor``, the counterparty always ``Comprador``).

A draft is built by our own code from a document invoicing already validated, so a draft that breaks a rule
here is a defect of that code (``FiscalDraftInvalidError``, internal), never a client's input: what a channel
objects to in a valid draft is an ``Objection`` (``channels``). Parties, lines and fields are data of the
tenant and of persons: a draft's ``repr`` names its document, version, channel and kind only, a party's its
tax id masked, a line's no description.

A draft travels as the platform codec's JSON (``ledger_platform.data.codec.encode``) and reads back with
``FiscalDraft.read``.
"""

import copy
import zoneinfo
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Final, NewType, final, override
from uuid import UUID
lazy from collections.abc import Mapping
lazy from datetime import date
lazy from decimal import Decimal

from pydantic import TypeAdapter, ValidationError

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_kernel.money import Currency
from ledger_kernel.regimes import DocumentKind
from ledger_kernel.tenancy import Tenant
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.money import Money
lazy from ledger_kernel.regimes import Direction, RegimeKey, TaxCategoryKey, TaxCategoryKind, Totals
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.data.codec import JsonValue

DocumentRef = NewType("DocumentRef", UUID)
"""A commercial document's id (invoicing's ``InvoiceId``, unwrapped): the kit never imports a module."""
_NOTES: Final = frozenset({DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE})


class FiscalDraftInvalidError(InvalidValueError):
    """A draft that could be no fiscal document's input: a defect of the code that built it."""

    code: ClassVar[str] = "fiscal_draft_invalid"

    def __init__(self, *, field: str | None, requirement: str) -> None:
        super().__init__(
            field="fiscal_draft" if field is None else f"fiscal_draft.{field}", requirement=requirement
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalParty:
    """A party as the document names it (see the module)."""

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
    """A document this one refers to: the invoice a credit or debit note corrects.

    Only the commercial document: the channel finds the number its authority gave it in its own records (DO:
    the e-NCF of its accepted document for that invoice, in the same mode). A paper NCF a document replaces
    is a channel field (DO: ``fiscal.do.replaces_ncf``), and so is why a note corrects (DO:
    ``fiscal.do.modification_code``).
    """

    document: DocumentRef
    issue_date: date


def _numbered(lines: tuple[FiscalLine, ...]) -> bool:
    return [line.number for line in lines] == list(range(1, len(lines) + 1))


def _is_time_zone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except ValueError, zoneinfo.ZoneInfoNotFoundError:
        return False
    return True


def _with_currencies(found: JsonValue, *, top: bool = True) -> object:
    """``found``, a draft's JSON form, with each currency code read as its ``Currency`` (``fields`` aside)."""
    match found:
        case dict():
            return {
                key: Currency.of(value)
                if key == "currency" and isinstance(value, str)
                else value
                if top and key == "fields"
                else _with_currencies(value, top=False)
                for key, value in found.items()
            }
        case list():
            return [_with_currencies(item, top=False) for item in found]
        case _:
            return found


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
    issuer: FiscalParty
    """The issuing company as it was when the document was issued; its primary tax id is required."""
    establishment_code: str
    time_zone: str
    """The IANA zone the issue date and the tax point are days of (rule 12, §7.1)."""
    counterparty: FiscalParty | None
    """The other party, if the document names one (see the module)."""
    lines: tuple[FiscalLine, ...]
    """One or more, numbered from 1 in order, every amount in ``currency``."""
    totals: Totals
    """The kernel calculator's, in ``currency``."""
    currency: Currency
    exchange_rate: Decimal | None
    """Units of the functional currency per one of ``currency``, more than 0; given with
    ``functional_totals`` only."""
    functional_totals: Totals | None
    """In the company's functional currency, another than ``currency``."""
    references: tuple[FiscalReference, ...] = ()
    """One or more for a credit or debit note, none for an invoice."""
    fields: Mapping[str, JsonValue] = field(hash=False)
    """This regime's country fields only (``fiscal.<regime>``, one level down): a deep copy, read-only at its
    top level."""
    issue_date: date
    tax_point: date
    external_reference: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise FiscalDraftInvalidError(field="version", requirement="1 or more")
        if self.issuer.tax_id is None:
            raise FiscalDraftInvalidError(
                field="issuer.tax_id", requirement="the issuing company's primary tax id"
            )
        if not _is_time_zone(self.time_zone):
            raise FiscalDraftInvalidError(
                field="time_zone", requirement="an IANA time zone this system knows"
            )
        self._require_lines()
        self._require_currencies()
        self._require_references()
        object.__setattr__(self, "fields", MappingProxyType(copy.deepcopy(dict(self.fields))))

    def _require_lines(self) -> None:
        if not self.lines:
            raise FiscalDraftInvalidError(field="lines", requirement="one line or more")
        if not _numbered(self.lines):
            raise FiscalDraftInvalidError(field="lines", requirement="numbered 1 to n in order")

    def _require_currencies(self) -> None:
        in_currency = "in the document's currency"
        if self.totals.gross.currency != self.currency:
            raise FiscalDraftInvalidError(field="totals", requirement=in_currency)
        for index, line in enumerate(self.lines):
            if line.base.currency != self.currency:
                raise FiscalDraftInvalidError(field=f"lines[{index}].base", requirement=in_currency)
            for position, tax in enumerate(line.taxes):
                if tax.amount.currency != self.currency:
                    raise FiscalDraftInvalidError(
                        field=f"lines[{index}].taxes[{position}].amount", requirement=in_currency
                    )
        rate, functional = self.exchange_rate, self.functional_totals
        if rate is None or functional is None:
            if rate is not None or functional is not None:
                raise FiscalDraftInvalidError(
                    field="exchange_rate", requirement="given with the functional totals, and only with them"
                )
            return
        if rate <= 0:
            raise FiscalDraftInvalidError(field="exchange_rate", requirement="more than 0")
        if functional.gross.currency == self.currency:
            raise FiscalDraftInvalidError(
                field="functional_totals", requirement="in a currency other than the document's"
            )

    def _require_references(self) -> None:
        if self.kind in _NOTES and not self.references:
            raise FiscalDraftInvalidError(field="references", requirement="one or more for a note")
        if self.kind not in _NOTES and self.references:
            raise FiscalDraftInvalidError(field="references", requirement="none for an invoice")

    @property
    def scope(self) -> Tenant:
        """The tenant the document belongs to: the scope of every unit and call about it."""
        return Tenant(self.organization, self.company, self.mode)

    @classmethod
    def read(cls, found: JsonValue) -> FiscalDraft:
        """The draft whose JSON form (the platform codec's) is ``found``.

        ``FiscalDraftInvalidError`` when ``found`` is no draft's JSON form, or names a draft that breaks one
        of the rules above.
        """
        try:
            return _DRAFT.validate_python(_with_currencies(found))
        except FiscalDraftInvalidError:
            raise
        except (ValidationError, LedgerError) as error:
            raise FiscalDraftInvalidError(field=None, requirement="a fiscal draft's JSON form") from error

    @override
    def __repr__(self) -> str:
        shown = f"version={self.version}, channel={self.channel!r}, kind={self.kind.value!r}"
        return f"FiscalDraft(document={self.document!r}, {shown})"


_DRAFT: Final = TypeAdapter(FiscalDraft)
