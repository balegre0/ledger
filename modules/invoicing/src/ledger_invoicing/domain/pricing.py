"""What a document charges: its terms as the client wrote them, priced with the company's regime (§7.4, §7.5).

``Terms`` are what the client sent, checked for shape; ``price`` resolves them once against the regime of the
company's jurisdiction, at the document's tax point (rule 12: never ``now()``):

- the document's currency (and the functional one) must have as many minor-unit places as the regime rounds
  to (Decision 8);
- the issue date defaults to today in the establishment's time zone and is never after it, the tax point
  defaults to the issue date and lies on it or at most ``MAX_TAX_POINT_AGE`` before it (Decision 6); a note's
  defaults to the tax point of the invoice it corrects instead, however old (it credits or debits at the
  rate the invoice charged), and one it names is bounded as any document's;
- each line's base is ``quantity x unit_price - discount`` rounded half up to the currency's minor unit
  (Decision 4), and each tax category it names is resolved to a snapshot ``{category, kind, percent, since}``
  of the rate in effect on the tax point;
- the totals are the regime's ``TaxCalculator``'s over every line: invoicing never adds taxes up itself;
- a document in another currency than the company's functional one carries the exchange rate (units of the
  functional currency per one of the document's), and its functional totals are converted once, each amount
  rounded as the regime rounds (Decision 5);
- country fields (``fiscal.<regime>.<field>``) the regime declares are each of its kind and bounded: text and
  codes without invisible characters, decimals money can hold, objects of text fields only (Decision 12);
  another regime's namespace is refused. A key the regime does not declare (a channel's format-level field)
  is kept as sent once it is plain text, or an object of up to ``MAX_LINES`` fields (one per line, DO's
  ``withholdings``) each text or an object of up to ``MAX_FIELD_OBJECT`` text fields, every name and text
  free of invisible characters: whether it may be sent at all is the ``IssuePolicy`` chain's to judge (M3b
  Decision 24), and without a chain ``refuse_undeclared`` refuses it, as the use cases call it.

**Taxes on one line do not compound.** A line may name several categories, one of each tax (ITBIS and a
selective tax, say): each is charged on the line's whole base, never on a base that includes another
(``TaxLine``). A tax charged on top of another (DGII's ITBIS on a price that includes ISC) cannot be expressed
by the kernel yet, so no regime declares one; when one does, the kernel's calculator gains it first.

Every amount stays in money's range (below 10^18): a document whose amounts would leave it is refused
(``AmountOutOfRangeError``) without repeating them.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final, Literal, assert_type, final
from zoneinfo import ZoneInfo
lazy from collections.abc import Mapping
lazy from datetime import datetime
lazy from decimal import Decimal

from ledger_invoicing.domain.errors import (
    AmountOutOfRangeError,
    CurrencyNotSupportedByRegimeError,
    ExchangeRateRequiredError,
    ExchangeRateUnusedError,
    FieldInvalidError,
    FieldRequiredError,
    FieldUndeclaredInvalidError,
    FieldUnknownError,
    InvalidExchangeRateError,
    InvalidIssueDateError,
    InvalidLineError,
    InvalidLinesError,
    InvalidTaxPointError,
    TaxRateNotInEffectError,
)
from ledger_invoicing.domain.lines import bounded
from ledger_invoicing.domain.text import invisible
from ledger_kernel.dated import NotInEffectError
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.money import InvalidAmountError, Money
from ledger_kernel.regimes import CategoryTotal, FieldKind, TaxCalculator, TaxCategoryKey, TaxLine, Totals
lazy from ledger_invoicing.domain.lines import LineTerms
lazy from ledger_invoicing.domain.parties import Party
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.regimes import (
    FieldDef,
    RegimeDef,
    RegimeKey,
    RoundingRule,
    TaxCategoryDef,
    TaxCategoryKind,
)

MAX_LINES: Final = 1000
"""Lines a document may have: a bound on what one request makes us compute and store."""
MAX_EXCHANGE_RATE_PLACES: Final = 12

type FieldValue = str | Mapping[str, FieldValue]
"""A country field's value as JSON brings it: text, or an object of them (``replaces_ncf``)."""
type CountryFields = Mapping[str, Mapping[str, FieldValue]]
"""``fiscal``: each regime's namespace and the fields under it (§9.8)."""

MAX_TAX_POINT_AGE: Final = timedelta(days=400)
"""How far before its issue date a document's tax point may be. Provisional: a regime rule replaces it (a law
says how late a supply may be invoiced); until one exists, a bound keeps a typo from reaching years back."""
MAX_FIELD_TEXT: Final = 500
"""The longest text a country field (or a field of an object one) holds."""
MAX_FIELD_CODE: Final = 100
MAX_FIELD_OBJECT: Final = 20
"""The most fields an object country field holds; each is text, never another object."""
_TEXT: Final = f"text of 1 to {MAX_FIELD_TEXT} characters, none of them invisible"
_CODE: Final = f"text of 1 to {MAX_FIELD_CODE} characters, none of them invisible"
_PLAIN_DECIMAL: Final = "a plain decimal number below 10^18 with at most 12 decimal places"
_OBJECT: Final = f"an object of at most {MAX_FIELD_OBJECT} fields named in lower case, each {_TEXT}"
_UNDECLARED: Final = (
    f"name or value is not {_TEXT}, or an object of at most {MAX_LINES} such fields (each text, or an object "
    f"of at most {MAX_FIELD_OBJECT} text fields)"
)
_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_DECIMAL: Final = re.compile(r"-?[0-9]{1,18}(?:\.[0-9]{1,12})?", re.ASCII)
_NOTHING: Final[Mapping[str, FieldValue]] = MappingProxyType({})
NO_FIELDS: Final[CountryFields] = MappingProxyType({})
"""No country fields: what a document sent none carries."""


def _frozen(value: FieldValue) -> FieldValue:
    """A read-only copy of ``value``: terms share nothing mutable with what the client's request built."""
    if isinstance(value, str):
        return value
    return MappingProxyType({key: _frozen(item) for key, item in value.items()})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EstablishmentRef:
    """The establishment a document is issued from, as it was: its code and the time zone of its dates."""

    code: str
    time_zone: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Terms:
    """A document's terms as the client wrote them; what is ``None`` defaults when they are priced.

    Built, they hold 1 to ``MAX_LINES`` lines each checked at its position (``LineTerms.checked``), a
    well-formed exchange rate if any, and a read-only copy of the country fields: they share nothing mutable
    with the request that built them.
    """

    currency: Currency
    lines: tuple[LineTerms, ...]
    exchange_rate: Decimal | None = None
    issue_date: date | None = None
    tax_point: date | None = None
    establishment: str | None = None
    """The code of the establishment the document is issued from; the company's ``MAIN`` when ``None``."""
    buyer: Party | None = None
    fiscal: CountryFields = field(default=NO_FIELDS, hash=False)

    def __post_init__(self) -> None:
        if not 1 <= len(self.lines) <= MAX_LINES:
            raise InvalidLinesError(MAX_LINES)
        rate = self.exchange_rate
        if rate is not None and not bounded(rate, places_at_most=MAX_EXCHANGE_RATE_PLACES, above_zero=True):
            raise InvalidExchangeRateError(MAX_EXCHANGE_RATE_PLACES)
        lines = tuple(line.checked(position) for position, line in enumerate(self.lines))
        fiscal = MappingProxyType(
            {
                namespace: MappingProxyType({name: _frozen(value) for name, value in values.items()})
                for namespace, values in self.fiscal.items()
            }
        )
        # Frozen, so `object.__setattr__`: the checked lines and the copied fields replace what was sent.
        object.__setattr__(self, "lines", lines)
        object.__setattr__(self, "fiscal", fiscal)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LineTax:
    """A tax a line is charged: the rate resolved at the tax point (kept for ever), and what it comes to.

    ``tax`` is this line's alone, rounded as the regime rounds a category; the document's tax is the
    calculator's over every line, which a regime that rounds per category does not split back into lines.
    """

    category: TaxCategoryKey
    kind: TaxCategoryKind
    percent: Decimal
    since: date
    """When the rate took effect: with the category, it names the rate in the regime's timeline."""
    tax: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PricedLine:
    """A line priced: its gross, its discount and its base in the document's currency, and its taxes."""

    terms: LineTerms
    gross: Money
    """``quantity x unit_price``, exact."""
    discount: Money
    base: Money
    """``gross - discount``, rounded half up to the currency's minor unit: what the line is taxed on."""
    taxes: tuple[LineTax, ...]
    """In the order the line names them."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Pricing:
    """Terms resolved against a regime: dates, lines, totals and country fields, stored with the document."""

    regime: RegimeKey
    establishment: EstablishmentRef
    issue_date: date
    tax_point: date
    lines: tuple[PricedLine, ...]
    totals: Totals
    """In the document's currency."""
    functional: Totals | None
    """In the company's functional currency, for a document in another one; ``None`` otherwise."""
    fiscal: Mapping[str, FieldValue] = field(hash=False)
    """The regime's country fields, as sent."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Dating:
    """What a document's dates are judged by when it is priced."""

    today: date
    """The date in the establishment's time zone: the issue date when the terms name none, and the latest."""
    tax_point: date | None = None
    """The tax point when the terms name none, taken as it is, however old: a note's is its invoice's. Without
    one, the tax point is the issue date."""


def local_date(instant: datetime, time_zone: str) -> date:
    """The date ``instant`` (aware) falls on in ``time_zone`` (an IANA name): an establishment's today.

    A naive ``instant`` would be read in the server's zone, so it is a defect of the caller.
    """
    if instant.utcoffset() is None:
        raise InvalidValueError(field="instant", requirement="an aware datetime")
    return instant.astimezone(ZoneInfo(time_zone)).date()


def price(
    terms: Terms,
    *,
    regime: RegimeDef,
    functional_currency: Currency,
    establishment: EstablishmentRef,
    dating: Dating,
) -> Pricing:
    """``terms`` priced with ``regime`` for a company keeping its books in ``functional_currency``.

    ``dating`` says what its dates default to and how late they may be. That a note is not dated before its
    invoice is checked where the invoice is read under its lock (``Invoice``).
    """
    places = regime.rounding.places
    for currency in (terms.currency, functional_currency):
        if currency.minor_units != places:
            raise CurrencyNotSupportedByRegimeError(currency.code, places)
    today = dating.today
    issue_date = today if terms.issue_date is None else terms.issue_date
    if issue_date > today:
        raise InvalidIssueDateError
    if terms.tax_point is not None:
        tax_point = terms.tax_point
        if not issue_date - MAX_TAX_POINT_AGE <= tax_point <= issue_date:
            raise InvalidTaxPointError(MAX_TAX_POINT_AGE.days)
    else:
        tax_point = issue_date if dating.tax_point is None else dating.tax_point
    rate = _exchange_rate(terms, functional_currency)
    fiscal = _country_fields(terms.fiscal, regime)
    categories = {category.key: category for category in regime.tax_categories}
    sinces = [_rates_of(position, line, categories, tax_point) for position, line in enumerate(terms.lines)]
    calculator = TaxCalculator(regime)
    pricer = _Pricer(
        currency=terms.currency, calculator=calculator, categories=categories, tax_point=tax_point
    )
    try:
        lines = tuple(
            pricer.line(position, line, since)
            for position, (line, since) in enumerate(zip(terms.lines, sinces, strict=True))
        )
        totals = calculator.totals(
            [TaxLine(base=line.base, categories=_keys(line.terms)) for line in lines], tax_point
        )
        functional = None if rate is None else _converted(totals, rate, functional_currency, regime.rounding)
    except InvalidAmountError as error:
        raise AmountOutOfRangeError from error
    return Pricing(
        regime=regime.key,
        establishment=establishment,
        issue_date=issue_date,
        tax_point=tax_point,
        lines=lines,
        totals=totals,
        functional=functional,
        fiscal=fiscal,
    )


def _exchange_rate(terms: Terms, functional_currency: Currency) -> Decimal | None:
    foreign = terms.currency != functional_currency
    if foreign and terms.exchange_rate is None:
        raise ExchangeRateRequiredError
    if not foreign and terms.exchange_rate is not None:
        raise ExchangeRateUnusedError
    return terms.exchange_rate


def _keys(line: LineTerms) -> tuple[TaxCategoryKey, ...]:
    """The categories ``line`` names, as the regime keys them."""
    return tuple(TaxCategoryKey(key) for key in line.taxes)


def _rates_of(
    position: int,
    line: LineTerms,
    categories: Mapping[TaxCategoryKey, TaxCategoryDef],
    tax_point: date,
) -> Mapping[TaxCategoryKey, date]:
    """When the rate of each category ``line`` names took effect, once each is of the regime and in effect.

    A line names one category of each tax (``<regime>.<tax>.<category>``): two ITBIS categories on one line
    are a contradiction, not two taxes.
    """
    taxes: set[str] = set()
    sinces: dict[TaxCategoryKey, date] = {}
    for index, key in enumerate(_keys(line)):
        category = categories.get(key)
        if category is None:
            raise InvalidLineError(position, "taxes", "a tax category of the company's regime", index=index)
        tax = key.rpartition(".")[0]
        if tax in taxes:
            raise InvalidLineError(position, "taxes", "one category of each tax")
        taxes.add(tax)
        try:
            sinces[key] = category.rates.entry_at(tax_point).since
        except NotInEffectError as error:
            raise TaxRateNotInEffectError(position, index) from error
    return sinces


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Pricer:
    """What prices each line of one document: its currency, calculator, categories and tax point."""

    currency: Currency
    calculator: TaxCalculator
    categories: Mapping[TaxCategoryKey, TaxCategoryDef]
    tax_point: date

    def line(self, position: int, line: LineTerms, sinces: Mapping[TaxCategoryKey, date]) -> PricedLine:
        """``line``, the one at ``position``, priced; ``sinces`` dates the rates of its categories."""
        gross = Money(line.unit_price, self.currency) * line.quantity
        discount = Money(line.discount, self.currency)
        if discount > gross:
            raise InvalidLineError(position, "discount", "at most the line's quantity times its unit price")
        base = (gross - discount).rounded()
        keys = _keys(line)
        alone = self.calculator.totals([TaxLine(base=base, categories=keys)], self.tax_point)
        charged = {total.category: total for total in alone.categories}
        taxes = tuple(
            LineTax(
                category=key,
                kind=self.categories[key].kind,
                percent=charged[key].percent,
                since=sinces[key],
                tax=charged[key].tax,
            )
            for key in keys
        )
        return PricedLine(terms=line, gross=gross, discount=discount, base=base, taxes=taxes)


def _converted(totals: Totals, rate: Decimal, currency: Currency, rule: RoundingRule) -> Totals:
    """``totals`` in ``currency`` at ``rate``: each amount converted once and rounded as ``rule`` says.

    The tax is the sum of the categories' converted taxes and the gross the converted net plus that tax, so
    the functional totals add up as the document's do.
    """

    def converted(amount: Money) -> Money:
        return Money(amount.times(rate, rule.places, rule.mode).amount, currency)

    categories = tuple(
        CategoryTotal(
            category=total.category,
            percent=total.percent,
            base=converted(total.base),
            tax=converted(total.tax),
        )
        for total in totals.categories
    )
    net = converted(totals.net)
    tax = sum((total.tax for total in categories), start=Money.zero(currency))
    return Totals(net=net, categories=categories, tax=tax, gross=net + tax)


def refuse_undeclared(pricing: Pricing, regime: RegimeDef) -> None:
    """Refuse a country field of ``pricing`` that ``regime`` does not declare (``FieldUnknownError``).

    What a document with no ``IssuePolicy`` chain to judge its other fields is held to (Decision 24).
    """
    declared = {definition.name for definition in regime.fields}
    if not declared.issuperset(pricing.fiscal):
        raise FieldUnknownError(regime.key, namespace=False)


def _country_fields(fiscal: CountryFields, regime: RegimeDef) -> Mapping[str, FieldValue]:
    """The regime's namespace as sent, once each value the regime declares is of its kind (see the module)."""
    key = regime.key
    if any(namespace != key for namespace in fiscal):
        raise FieldUnknownError(key, namespace=True)
    sent = fiscal.get(key, _NOTHING)
    declared = {definition.name for definition in regime.fields}
    for name, sent_value in sent.items():
        if name not in declared and not (_is_text(name, MAX_FIELD_TEXT) and _is_undeclared(sent_value)):
            raise FieldUndeclaredInvalidError(key, _UNDECLARED)
    for definition in regime.fields:
        value = sent.get(definition.name)
        if value is None:
            if definition.required:
                raise FieldRequiredError(key, definition.name)
            continue
        requirement = _requirement(definition, value)
        if requirement is not None:
            raise FieldInvalidError(key, definition.name, requirement)
    return sent


def _requirement(definition: FieldDef, value: FieldValue) -> str | None:
    """What ``value`` must be to be of ``definition``'s kind; ``None`` when it is."""
    kind = definition.kind
    match kind:
        case FieldKind.TEXT:
            return None if _is_text(value, MAX_FIELD_TEXT) else _TEXT
        case FieldKind.CODE:
            return None if _is_text(value, MAX_FIELD_CODE) else _CODE
        case FieldKind.DATE:
            return None if isinstance(value, str) and _is_date(value) else "a date (YYYY-MM-DD)"
        case FieldKind.DECIMAL:
            plain = isinstance(value, str) and _DECIMAL.fullmatch(value) is not None
            return None if plain else _PLAIN_DECIMAL
        case _:
            assert_type(kind, Literal[FieldKind.OBJECT])  # a new kind fails the type checkers here
            return None if _is_object(value) else _OBJECT


def _is_text(value: FieldValue, limit: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= limit and not invisible(value)


def _is_undeclared(value: FieldValue) -> bool:
    """Whether ``value`` is what an undeclared field may hold (see the module)."""
    if isinstance(value, str):
        return _is_text(value, MAX_FIELD_TEXT)
    return len(value) <= MAX_LINES and all(
        _is_text(name, MAX_FIELD_TEXT) and (_is_text(item, MAX_FIELD_TEXT) or _is_plain_object(item))
        for name, item in value.items()
    )


def _is_plain_object(value: FieldValue) -> bool:
    """Whether ``value`` is an object of at most ``MAX_FIELD_OBJECT`` text fields, any name of plain text."""
    if isinstance(value, str) or len(value) > MAX_FIELD_OBJECT:
        return False
    return all(
        _is_text(name, MAX_FIELD_TEXT) and _is_text(item, MAX_FIELD_TEXT) for name, item in value.items()
    )


def _is_object(value: FieldValue) -> bool:
    """Whether ``value`` is an object of at most ``MAX_FIELD_OBJECT`` text fields, named in lower case."""
    if isinstance(value, str) or len(value) > MAX_FIELD_OBJECT:
        return False
    return all(
        _NAME.fullmatch(name) is not None and _is_text(item, MAX_FIELD_TEXT) for name, item in value.items()
    )


def _is_date(text: str) -> bool:
    if _DATE.fullmatch(text) is None:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True
