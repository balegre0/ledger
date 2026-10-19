"""The pure regime schema (§8.2, ADR 0005): a jurisdiction's tax law as typed, dated data, and the calculator.

The one tax calculator (``TaxCalculator``) lives here too. Standard library only and no loading:
``ledger_platform.runtime.registries.Regimes`` reads packs from entry points, checks what needs more than the
standard library (that the time zone exists). A ``RegimeDef`` refuses, when it is built, a key other than its
jurisdiction's, duplicate keys, schemes or categories outside its jurisdiction, corrections that name no
document type, a rounding rule that cannot round and a pack without sources. Its mappings are read-only
copies.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, NewType, final
lazy from collections.abc import Callable, Iterable, Mapping, Sequence
lazy from datetime import date

from ledger_kernel.errors import InvalidInputError, InvalidValueError, LedgerError, excerpt
from ledger_kernel.money import MAX_SCALE, Money, RoundingMode
from ledger_kernel.tax_ids import (
    MAX_TAX_ID_LENGTH,
    InvalidTaxIdError,
    TaxId,
    TaxIdCheck,
    is_storable,
    parse_scheme,
)
lazy from ledger_kernel.dated import Timeline
lazy from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.tax_ids import TaxIdScheme

Locale = NewType("Locale", str)
"""A BCP 47 language tag: ``es``, ``en``, ``es-DO``."""
RegimeKey = NewType("RegimeKey", str)
"""Its jurisdiction in lower case: the country (``do``) or the subdivision whose law differs (``us-ca``)."""
TaxCategoryKey = NewType("TaxCategoryKey", str)
"""``<regime>.<tax>.<category>``: ``do.itbis.standard``."""

MAX_RATE_PLACES: Final = 6
"""Decimal places a rate in percent may have (``18.000001``): enough for any law, few enough to be exact."""
_TIME_ZONE: Final = re.compile(r"UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+){1,2}", re.ASCII)
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)


def _places(value: Decimal) -> int:
    """The decimal places ``value`` is written with (``18.50`` has two, ``1E+1`` none)."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _frozen[K, V](mapping: Mapping[K, V]) -> Mapping[K, V]:
    """A read-only copy: a definition shares no mutable state with the pack that built it."""
    return MappingProxyType(dict(mapping))


class InvalidRegimeError(LedgerError):
    """A regime pack that contradicts itself: a defect of the pack, found when it is built."""

    code: ClassVar[str] = "invalid_regime"

    def __init__(self, regime: str, problem: str) -> None:
        super().__init__(f"regime {regime!r} {problem}")


class TaxIdSchemeUnknownError(InvalidInputError):
    """No registered regime declares the scheme a client named."""

    code: ClassVar[str] = "tax_id_scheme_unknown"

    def __init__(self, scheme: str) -> None:
        super().__init__(f"no regime declares the tax id scheme {excerpt(scheme)}")


class TaxCategoryUnknownError(InvalidInputError):
    """A line names a tax category its regime does not declare."""

    code: ClassVar[str] = "tax_category_unknown"

    def __init__(self, category: str) -> None:
        super().__init__(f"the regime declares no tax category {excerpt(category)}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Where a regime's values come from: a law, a regulation, an authority's technical document."""

    title: str
    url: str
    version: str
    retrieved_on: date


class TaxCategoryKind(StrEnum):
    """The generic kind of a tax category, for formats (UBL) that speak generically."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    EXEMPT = "exempt"
    OUTSIDE_SCOPE = "outside_scope"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RateValue:
    """A rate in percent and where the law says so."""

    percent: Decimal
    source: Source

    def __post_init__(self) -> None:
        percent = self.percent
        in_range = type(percent) is Decimal and percent.is_finite() and _ZERO <= percent <= _HUNDRED
        if not in_range or _places(percent) > MAX_RATE_PLACES:
            raise InvalidValueError(
                field="rate.percent",
                requirement=f"a Decimal from 0 to 100 with at most {MAX_RATE_PLACES} places",
            )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxCategoryDef:
    """A tax category (``<regime>.<tax>.<category>``, e.g. ``do.itbis.standard``) and its dated rates."""

    key: TaxCategoryKey
    labels: Mapping[Locale, str] = field(hash=False)
    kind: TaxCategoryKind
    rates: Timeline[RateValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))


class DocumentKind(StrEnum):
    """The commercial kind of a document (§7.5)."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


class Direction(StrEnum):
    """Sales, or a self-billed purchase issued on a supplier's behalf."""

    SALES = "sales"
    PURCHASE = "purchase"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentTypeDef:
    """A document type the law defines (DGII's 31-47), and which types it may correct."""

    code: str
    labels: Mapping[Locale, str] = field(hash=False)
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    corrects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax id scheme: how a value is normalized and checked, and whether a checksum mismatch is refused."""

    key: TaxIdScheme
    country: CountryCode
    labels: Mapping[Locale, str] = field(hash=False)
    normalize: Callable[[str], str]
    check: Callable[[str], TaxIdCheck]
    check_digit_enforced: bool
    peppol_icd: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))


class FieldKind(StrEnum):
    """What a country field holds on the wire (``fiscal.<regime>.<field>``)."""

    TEXT = "text"
    CODE = "code"
    DATE = "date"
    DECIMAL = "decimal"
    OBJECT = "object"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FieldDef:
    """A law-level field a regime declares under ``fiscal.<regime>``."""

    name: str
    labels: Mapping[Locale, str] = field(hash=False)
    kind: FieldKind
    required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))


class RoundingPoint(StrEnum):
    """Where amounts are rounded: each line's tax, each category's total, the document's total."""

    LINE = "line"
    CATEGORY = "category"
    DOCUMENT = "document"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RoundingRule:
    """How and where a regime rounds, and how far a document may stray per line (channels verify with it)."""

    mode: RoundingMode
    points: frozenset[RoundingPoint]
    places: int
    line_tolerance: Decimal

    def __post_init__(self) -> None:
        # A pack is data: a rule that cannot round is refused when the pack is built, not on a document.
        if type(self.mode) is not RoundingMode:
            raise InvalidValueError(field="rounding.mode", requirement="a RoundingMode")
        points = self.points
        if type(points) is not frozenset or not all(type(point) is RoundingPoint for point in points):
            raise InvalidValueError(field="rounding.points", requirement="a frozenset of RoundingPoint")
        if type(self.places) is not int or not 0 <= self.places <= MAX_SCALE:
            raise InvalidValueError(field="rounding.places", requirement=f"an int from 0 to {MAX_SCALE}")
        tolerance = self.line_tolerance
        if type(tolerance) is not Decimal or not tolerance.is_finite() or tolerance < _ZERO:
            raise InvalidValueError(field="rounding.line_tolerance", requirement="a finite Decimal from 0")


def _unique(regime: str, what: str, keys: Iterable[str]) -> frozenset[str]:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise InvalidRegimeError(regime, f"declares {what} {key!r} twice")
        seen.add(key)
    return frozenset(seen)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RegimeDef:
    """A jurisdiction's tax law as data (§8.2)."""

    key: RegimeKey
    jurisdiction: Jurisdiction
    currency: Currency
    time_zone: str
    labels: Mapping[Locale, str] = field(hash=False)
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    rounding: RoundingRule
    fields: tuple[FieldDef, ...]
    parameters: Mapping[str, Timeline[Decimal | int | str]] = field(hash=False)
    sources: tuple[Source, ...]
    functional_currencies: tuple[Currency, ...] = ()
    """Currencies a company of the jurisdiction may keep its books in besides ``currency`` (where the law lets
    some keep them in a foreign currency); none by default."""
    foreign_tax_ids: bool = False
    """Whether a company of the jurisdiction may list tax ids of other countries' schemes (a foreign company's
    own ids beside its local one); by default it lists its own country's only."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", _frozen(self.labels))
        object.__setattr__(self, "parameters", _frozen(self.parameters))
        self._check_identity()
        self._check_schemes()
        self._check_contents()

    def keeps_books_in(self, currency: Currency) -> bool:
        """Whether a company of the jurisdiction may keep its books in ``currency``."""
        return currency == self.currency or currency in self.functional_currencies

    def accepts_tax_id(self, tax_id: TaxId) -> bool:
        """Whether a company of the jurisdiction may list ``tax_id``: its country's, or any where allowed."""
        return self.foreign_tax_ids or tax_id.scheme.partition(".")[0] == self.jurisdiction.country.lower()

    def _check_identity(self) -> None:
        key, jurisdiction = self.key, self.jurisdiction
        if key != (jurisdiction.subdivision or jurisdiction.country).lower():
            raise InvalidRegimeError(key, "is keyed outside its jurisdiction")
        if _TIME_ZONE.fullmatch(self.time_zone) is None:
            raise InvalidRegimeError(key, "has a time zone that is not an IANA name")
        if not self.labels:
            raise InvalidRegimeError(key, "has no label")
        if not self.sources:
            raise InvalidRegimeError(key, "names no source")
        books = self.functional_currencies
        if self.currency in books or len(set(books)) != len(books):
            raise InvalidRegimeError(key, "lists a functional currency twice, or its own currency among them")

    def _check_schemes(self) -> None:
        key, country = self.key, self.jurisdiction.country
        _unique(key, "tax id scheme", (scheme.key for scheme in self.tax_id_schemes))
        for scheme in self.tax_id_schemes:
            if scheme.country != country or not scheme.key.startswith(f"{country.lower()}."):
                raise InvalidRegimeError(key, "declares a tax id scheme of another country")

    def _check_contents(self) -> None:
        key = self.key
        _unique(key, "tax category", (category.key for category in self.tax_categories))
        for category in self.tax_categories:
            if not category.key.startswith(f"{key}."):
                raise InvalidRegimeError(key, "declares a tax category outside its key")
        codes = _unique(key, "document type", (document.code for document in self.document_types))
        for document in self.document_types:
            if not codes.issuperset(document.corrects):
                raise InvalidRegimeError(
                    key, f"has document type {document.code!r} correcting an unknown type"
                )
        _unique(key, "field", (field_def.name for field_def in self.fields))


@final
@dataclass(frozen=True, slots=True)
class ParsedTaxId:
    """A tax id a scheme accepted, and what its check said (``CHECKSUM_MISMATCH`` is a warning here)."""

    tax_id: TaxId
    check: TaxIdCheck


@final
class TaxIdSchemes:
    """Every scheme of the registered regimes: reads a client's ``{scheme, value}``."""

    __slots__ = ("_by_key",)

    def __init__(self, schemes: Iterable[TaxIdSchemeDef]) -> None:
        by_key: dict[str, TaxIdSchemeDef] = {}
        for scheme in schemes:
            if scheme.key in by_key:
                raise InvalidRegimeError(
                    scheme.key.partition(".")[0], f"declares tax id scheme {scheme.key!r} twice"
                )
            by_key[scheme.key] = scheme
        self._by_key: Mapping[str, TaxIdSchemeDef] = MappingProxyType(by_key)

    def parse(self, scheme: object, value: object) -> ParsedTaxId:
        """Normalize and check ``value`` with ``scheme``'s rules; a refusal never repeats the value.

        Both arrive from a client as whatever was sent: anything but text is refused.
        """
        key = parse_scheme(scheme)
        definition = self._by_key.get(key)
        if definition is None:
            raise TaxIdSchemeUnknownError(key)
        if type(value) is not str or len(value) > MAX_TAX_ID_LENGTH:
            raise InvalidTaxIdError(key, TaxIdCheck.INVALID_FORMAT)
        normalized = definition.normalize(value)
        if not is_storable(normalized):  # "---" normalizes to "": the client's format error, not our defect
            raise InvalidTaxIdError(key, TaxIdCheck.INVALID_FORMAT)
        check = definition.check(normalized)
        refused = check is TaxIdCheck.INVALID_FORMAT or (
            check is TaxIdCheck.CHECKSUM_MISMATCH and definition.check_digit_enforced
        )
        if refused:
            raise InvalidTaxIdError(key, check)
        return ParsedTaxId(TaxId(key, normalized), check)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxLine:
    """A line's taxable base and the categories taxed on it: one or more, each charged on the whole base.

    A line taxed twice (a sales tax and a selective consumption tax, a tip) lists both categories; its base is
    counted once in the net. Taxes stacked this way do not compound: a tax charged on a base that already
    includes another is not expressible yet.
    """

    base: Money
    categories: tuple[TaxCategoryKey, ...]

    def __post_init__(self) -> None:
        if not self.categories or len(set(self.categories)) != len(self.categories):
            raise InvalidValueError(
                field="tax_line.categories", requirement="one or more distinct categories"
            )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryTotal:
    """One category's base and tax on a document."""

    category: TaxCategoryKey
    percent: Decimal
    base: Money
    tax: Money


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Totals:
    """A document's net, its taxes per category, its tax and its gross; ``gross == net + tax``."""

    net: Money
    categories: tuple[CategoryTotal, ...]
    tax: Money
    gross: Money


@final
class TaxCalculator:
    """The one tax calculator (§8.2): invoicing computes with it, channels verify with it."""

    __slots__ = ("_categories", "_regime")

    def __init__(self, regime: RegimeDef) -> None:
        self._regime = regime
        self._categories: Mapping[TaxCategoryKey, TaxCategoryDef] = MappingProxyType(
            {category.key: category for category in regime.tax_categories}
        )

    def totals(self, lines: Sequence[TaxLine], tax_point: date) -> Totals:
        """The totals of ``lines`` at the rates in effect on ``tax_point``, rounded where the regime says.

        Every line is in one currency (``CurrencyMismatchError`` otherwise); no lines total zero in the
        regime's. Categories are listed by key, so the order of the lines, and of a line's categories, never
        changes the result. The net is the sum of the lines' bases, each counted once however many categories
        tax it.

        A line's tax in a category is its base times the rate, computed exactly and rounded once: to the
        rule's places where the regime rounds each line, to the finest scale money holds otherwise. Any base
        money holds is taxed without an error (a 24-place base times a 6-place rate has 32 places: it is never
        left unrounded).
        """
        rule = self._regime.rounding
        currency = lines[0].base.currency if lines else self._regime.currency
        zero = Money.zero(currency)
        line_places = rule.places if RoundingPoint.LINE in rule.points else MAX_SCALE
        bases: dict[TaxCategoryKey, Money] = {}
        taxes: dict[TaxCategoryKey, Money] = {}
        percents: dict[TaxCategoryKey, Decimal] = {}
        for line in lines:
            for key in line.categories:
                category = self._categories.get(key)
                if category is None:
                    raise TaxCategoryUnknownError(key)
                percent = category.rates.at(tax_point).percent
                tax = line.base.times(percent.scaleb(-2), line_places, rule.mode)
                bases[category.key] = bases.get(category.key, zero) + line.base
                taxes[category.key] = taxes.get(category.key, zero) + tax
                percents[category.key] = percent
        categories = tuple(
            CategoryTotal(
                category=key,
                percent=percents[key],
                base=bases[key],
                tax=taxes[key].quantized(rule.places, rule.mode)
                if RoundingPoint.CATEGORY in rule.points
                else taxes[key],
            )
            for key in sorted(bases)
        )
        net = sum((line.base for line in lines), start=zero)  # a base taxed twice is still one base
        tax_total = sum((category.tax for category in categories), start=zero)
        if RoundingPoint.DOCUMENT in rule.points:
            tax_total = tax_total.quantized(rule.places, rule.mode)
        return Totals(net=net, categories=categories, tax=tax_total, gross=net + tax_total)
