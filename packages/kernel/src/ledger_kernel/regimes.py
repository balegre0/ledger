"""The pure regime schema (§8.2, ADR 0005): a jurisdiction's tax law as typed, dated data, and the calculator.

The one tax calculator (``TaxCalculator``) lives here too. Standard library only and no loading:
``ledger_platform.runtime.registries.Regimes`` reads packs from entry points, checks what needs more than the
standard library (that the time zone exists) and freezes them. A ``RegimeDef`` refuses, when it is built,
duplicate keys, schemes or categories outside its jurisdiction, corrections that name no document type, and a
pack without sources.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, NewType, final
lazy from collections.abc import Callable, Iterable, Mapping, Sequence
lazy from datetime import date

from ledger_kernel.errors import InvalidInputError, InvalidValueError, LedgerError, excerpt
from ledger_kernel.money import Money
from ledger_kernel.tax_ids import MAX_TAX_ID_LENGTH, InvalidTaxIdError, TaxId, TaxIdCheck, parse_scheme
lazy from ledger_kernel.dated import Timeline
lazy from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
lazy from ledger_kernel.money import Currency, RoundingMode
lazy from ledger_kernel.tax_ids import TaxIdScheme

Locale = NewType("Locale", str)
"""A BCP 47 language tag: ``es``, ``en``, ``es-DO``."""
RegimeKey = NewType("RegimeKey", str)
"""``<cc>`` or ``<cc>-<subdivision>`` when the law differs inside a country: ``do``, ``us-ca``."""

_REGIME_KEY: Final = re.compile(r"[a-z]{2}(?:-[a-z0-9]{1,3})?", re.ASCII)
_TIME_ZONE: Final = re.compile(r"UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+){1,2}", re.ASCII)
_ZERO: Final = Decimal(0)
_HUNDRED: Final = Decimal(100)


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
        if type(percent) is not Decimal or not percent.is_finite() or not _ZERO <= percent <= _HUNDRED:
            raise InvalidValueError(field="rate.percent", requirement="a finite Decimal from 0 to 100")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxCategoryDef:
    """A tax category (``<regime>.<tax>.<category>``, e.g. ``do.itbis.standard``) and its dated rates."""

    key: str
    labels: Mapping[Locale, str]
    kind: TaxCategoryKind
    rates: Timeline[RateValue]


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
    labels: Mapping[Locale, str]
    kind: DocumentKind
    direction: Direction
    requires_buyer_tax_id: bool
    corrects: tuple[str, ...] = ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TaxIdSchemeDef:
    """A tax id scheme: how a value is normalized and checked, and whether a checksum mismatch is refused."""

    key: TaxIdScheme
    country: CountryCode
    labels: Mapping[Locale, str]
    normalize: Callable[[str], str]
    check: Callable[[str], TaxIdCheck]
    check_digit_enforced: bool
    peppol_icd: str | None = None


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
    labels: Mapping[Locale, str]
    kind: FieldKind
    required: bool = False


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
    labels: Mapping[Locale, str]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    rounding: RoundingRule
    fields: tuple[FieldDef, ...]
    parameters: Mapping[str, Timeline[Decimal | int | str]]
    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        self._check_identity()
        self._check_schemes()
        self._check_contents()

    def _check_identity(self) -> None:
        key, country = self.key, self.jurisdiction.country
        if _REGIME_KEY.fullmatch(key) is None:
            raise InvalidRegimeError(key, "has a key that is not <cc> or <cc>-<subdivision>")
        if not key.startswith(country.lower()):
            raise InvalidRegimeError(key, "is keyed outside its jurisdiction")
        if _TIME_ZONE.fullmatch(self.time_zone) is None:
            raise InvalidRegimeError(key, "has a time zone that is not an IANA name")
        if not self.labels:
            raise InvalidRegimeError(key, "has no label")
        if not self.sources:
            raise InvalidRegimeError(key, "names no source")

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

    def parse(self, scheme: str, value: object) -> ParsedTaxId:
        """Normalize and check ``value`` with ``scheme``'s rules; a refusal never repeats the value."""
        key = parse_scheme(scheme)
        definition = self._by_key.get(key)
        if definition is None:
            raise TaxIdSchemeUnknownError(key)
        if type(value) is not str or len(value) > MAX_TAX_ID_LENGTH:
            raise InvalidTaxIdError(key, TaxIdCheck.INVALID_FORMAT)
        normalized = definition.normalize(value)
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
    """A line's taxable base and the category it is taxed in."""

    base: Money
    category: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryTotal:
    """One category's base and tax on a document."""

    category: str
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
        self._categories: Mapping[str, TaxCategoryDef] = MappingProxyType(
            {category.key: category for category in regime.tax_categories}
        )

    def totals(self, lines: Sequence[TaxLine], tax_point: date) -> Totals:
        """The totals of ``lines`` at the rates in effect on ``tax_point``, rounded where the regime says.

        Every line is in one currency (``CurrencyMismatchError`` otherwise); no lines total zero in the
        regime's.
        Categories are listed by key, so the order of the lines never changes the result.
        """
        rule = self._regime.rounding
        currency = lines[0].base.currency if lines else self._regime.currency
        zero = Money.zero(currency)
        bases: dict[str, Money] = {}
        taxes: dict[str, Money] = {}
        percents: dict[str, Decimal] = {}
        for line in lines:
            category = self._categories.get(line.category)
            if category is None:
                raise TaxCategoryUnknownError(line.category)
            percent = category.rates.at(tax_point).percent
            tax = line.base * percent.scaleb(-2)
            if RoundingPoint.LINE in rule.points:
                tax = tax.quantized(rule.places, rule.mode)
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
        net = sum((category.base for category in categories), start=zero)
        tax_total = sum((category.tax for category in categories), start=zero)
        if RoundingPoint.DOCUMENT in rule.points:
            tax_total = tax_total.quantized(rule.places, rule.mode)
        return Totals(net=net, categories=categories, tax=tax_total, gross=net + tax_total)
