import operator
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, TypedDict, Unpack
lazy from collections.abc import Callable, Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import MAX_SCALE, Currency, CurrencyMismatchError, Money, RoundingMode
from ledger_kernel.regimes import (
    CategoryTotal,
    Direction,
    DocumentKind,
    DocumentTypeDef,
    FieldDef,
    FieldKind,
    InvalidRegimeError,
    Locale,
    RateValue,
    RegimeDef,
    RegimeKey,
    RoundingPoint,
    RoundingRule,
    Source,
    TaxCalculator,
    TaxCategoryDef,
    TaxCategoryKey,
    TaxCategoryKind,
    TaxCategoryUnknownError,
    TaxIdSchemeDef,
    TaxIdSchemes,
    TaxIdSchemeUnknownError,
    TaxLine,
)
from ledger_kernel.tax_ids import InvalidTaxIdError, InvalidTaxIdSchemeError, TaxId, TaxIdCheck, TaxIdScheme

SOURCE: Final = Source(
    title="Probe tax law", url="https://law.invalid/probe", version="1", retrieved_on=date(2026, 10, 13)
)
USD: Final = Currency.of("USD")
DOP: Final = Currency.of("DOP")
LABELS: Final[Mapping[Locale, str]] = {Locale("en"): "Probe"}
DAY: Final = date(2026, 1, 1)
US: Final = Jurisdiction(CountryCode("US"))
CALIFORNIA: Final = Jurisdiction(CountryCode("US"), "US-CA")
STANDARD_KEY: Final = TaxCategoryKey("us.sales.standard")
REDUCED_KEY: Final = TaxCategoryKey("us.sales.reduced")


def digits_check(value: str) -> TaxIdCheck:
    if len(value) != 4 or not value.isascii() or not value.isdigit():
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if sum(map(int, value)) % 2 == 0 else TaxIdCheck.CHECKSUM_MISMATCH


def anything_goes(value: str) -> TaxIdCheck:
    del value
    return TaxIdCheck.VALID


def without_dashes(text: str) -> str:
    return text.replace("-", "")


def scheme(
    *,
    enforced: bool,
    key: str = "us.probe",
    country: str = "US",
    check: Callable[[str], TaxIdCheck] = digits_check,
) -> TaxIdSchemeDef:
    return TaxIdSchemeDef(
        key=TaxIdScheme(key),
        country=CountryCode(country),
        labels=LABELS,
        normalize=without_dashes,
        check=check,
        check_digit_enforced=enforced,
    )


def category(key: str, percent: str) -> TaxCategoryDef:
    return TaxCategoryDef(
        key=TaxCategoryKey(key),
        labels=LABELS,
        kind=TaxCategoryKind.STANDARD,
        rates=Timeline(Since(date(2020, 1, 1), RateValue(percent=Decimal(percent), source=SOURCE))),
    )


def document(code: str, *, corrects: tuple[str, ...] = ()) -> DocumentTypeDef:
    kind = DocumentKind.CREDIT_NOTE if corrects else DocumentKind.INVOICE
    return DocumentTypeDef(
        code=code,
        labels=LABELS,
        kind=kind,
        direction=Direction.SALES,
        requires_buyer_tax_id=False,
        corrects=corrects,
    )


STANDARD: Final = category("us.sales.standard", "18")
REDUCED: Final = category("us.sales.reduced", "16")


class Changes(TypedDict, total=False):
    """What one test changes of the fixture regime: every other field keeps its fixture value."""

    points: frozenset[RoundingPoint]
    mode: RoundingMode
    key: str
    jurisdiction: Jurisdiction
    time_zone: str
    labels: Mapping[Locale, str]
    sources: tuple[Source, ...]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    fields: tuple[FieldDef, ...]


def regime(**changes: Unpack[Changes]) -> RegimeDef:
    rounding = RoundingRule(
        mode=changes.get("mode", RoundingMode.HALF_UP),
        points=changes.get("points", frozenset({RoundingPoint.LINE})),
        places=2,
        line_tolerance=Decimal("0.01"),
    )
    return RegimeDef(
        key=RegimeKey(changes.get("key", "us")),
        jurisdiction=changes.get("jurisdiction", US),
        currency=USD,
        time_zone=changes.get("time_zone", "America/New_York"),
        labels=changes.get("labels", LABELS),
        tax_id_schemes=changes.get("tax_id_schemes", (scheme(enforced=False),)),
        tax_categories=changes.get("tax_categories", (STANDARD, REDUCED)),
        document_types=changes.get("document_types", (document("1"), document("2", corrects=("1",)))),
        rounding=rounding,
        fields=changes.get("fields", ()),
        parameters={"threshold": Timeline(Since(date(2020, 1, 1), Decimal(1000)))},
        sources=changes.get("sources", (SOURCE,)),
    )


def line(amount: str, key: TaxCategoryKey = STANDARD_KEY, currency: Currency = USD) -> TaxLine:
    return TaxLine(base=Money.of(amount, currency), category=key)


# ── Tax ids ───────────────────────────────────────────────────────────────────────


def test_a_checksum_mismatch_is_a_warning_unless_the_scheme_enforces_it() -> None:
    lenient = TaxIdSchemes((scheme(enforced=False),))
    parsed = lenient.parse("us.probe", "12-35")
    assert parsed.tax_id == TaxId(TaxIdScheme("us.probe"), "1235")
    assert parsed.check is TaxIdCheck.CHECKSUM_MISMATCH
    with pytest.raises(InvalidTaxIdError):
        TaxIdSchemes((scheme(enforced=True),)).parse("us.probe", "1235")


def test_a_valid_value_is_valid_whether_or_not_the_scheme_enforces_its_check_digit() -> None:
    parsed = TaxIdSchemes((scheme(enforced=True),)).parse("us.probe", "1-2-3-4")
    assert (parsed.tax_id.value, parsed.check) == ("1234", TaxIdCheck.VALID)


def test_a_bad_format_and_an_unknown_scheme_are_refused() -> None:
    schemes = TaxIdSchemes((scheme(enforced=False),))
    with pytest.raises(InvalidTaxIdError):
        schemes.parse("us.probe", "12a4")
    with pytest.raises(InvalidTaxIdError):
        schemes.parse("us.probe", "1" * 41)
    with pytest.raises(TaxIdSchemeUnknownError):
        schemes.parse("us.other", "1234")


def test_a_scheme_or_a_value_that_is_not_text_is_refused() -> None:
    schemes = TaxIdSchemes((scheme(enforced=False),))
    with pytest.raises(InvalidTaxIdError, match="invalid_format"):
        schemes.parse("us.probe", 1234)  # the boundary hands over whatever arrived
    with pytest.raises(InvalidTaxIdSchemeError):
        schemes.parse(7, "1234")


@pytest.mark.parametrize("value", ["---", "-", "12 - 34"])
def test_a_value_its_normalization_leaves_unstorable_is_a_bad_format(value: str) -> None:
    schemes = TaxIdSchemes((scheme(enforced=True, check=anything_goes),))
    with pytest.raises(InvalidTaxIdError, match=r"^not a valid us\.probe identifier: invalid_format$"):
        schemes.parse("us.probe", value)


def test_two_regimes_declaring_one_scheme_are_refused() -> None:
    with pytest.raises(InvalidRegimeError, match=r"^regime 'us' declares tax id scheme 'us\.probe' twice$"):
        TaxIdSchemes((scheme(enforced=False), scheme(enforced=True)))


# ── Regime definitions ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("built", "problem"),
    [
        (lambda: regime(key="usa"), "is keyed outside its jurisdiction"),
        (lambda: regime(key="mx"), "is keyed outside its jurisdiction"),
        (lambda: regime(key="us-ny", jurisdiction=CALIFORNIA), "is keyed outside its jurisdiction"),
        (lambda: regime(key="us", jurisdiction=CALIFORNIA), "is keyed outside its jurisdiction"),
        (lambda: regime(key="us-ca", jurisdiction=US), "is keyed outside its jurisdiction"),
        (lambda: regime(time_zone="not a zone"), "has a time zone that is not an IANA name"),
        (lambda: regime(labels={}), "has no label"),
        (lambda: regime(sources=()), "names no source"),
        (
            lambda: regime(tax_id_schemes=(scheme(enforced=False), scheme(enforced=True))),
            "declares tax id scheme 'us.probe' twice",
        ),
        (
            lambda: regime(tax_id_schemes=(scheme(enforced=False, key="mx.probe", country="MX"),)),
            "declares a tax id scheme of another country",
        ),
        (
            lambda: regime(tax_id_schemes=(scheme(enforced=False, key="mx.probe"),)),
            "declares a tax id scheme of another country",
        ),
        (
            lambda: regime(tax_categories=(category("mx.sales.standard", "1"),)),
            "declares a tax category outside its key",
        ),
        (
            lambda: regime(
                tax_categories=(category("us.sales.standard", "1"), category("us.sales.standard", "2"))
            ),
            "declares tax category 'us.sales.standard' twice",
        ),
        (
            lambda: regime(document_types=(document("2", corrects=("9",)),)),
            "has document type '2' correcting an unknown type",
        ),
        (lambda: regime(document_types=(document("1"), document("1"))), "declares document type '1' twice"),
        (
            lambda: regime(
                fields=(
                    FieldDef(name="ncf", labels=LABELS, kind=FieldKind.CODE),
                    FieldDef(name="ncf", labels=LABELS, kind=FieldKind.TEXT, required=True),
                )
            ),
            "declares field 'ncf' twice",
        ),
    ],
)
def test_a_regime_that_contradicts_itself_is_refused(built: Callable[[], RegimeDef], problem: str) -> None:
    with pytest.raises(InvalidRegimeError, match=problem):
        built()


def test_a_regime_of_a_subdivision_is_keyed_by_the_subdivision() -> None:
    california = regime(
        key="us-ca", jurisdiction=CALIFORNIA, tax_categories=(category("us-ca.sales.standard", "7.25"),)
    )
    assert california.key == "us-ca"


def test_a_definition_is_an_immutable_value() -> None:
    labels = {Locale("en"): "Probe"}
    built = regime(labels=labels)
    labels[Locale("es")] = "Sonda"
    assert dict(built.labels) == {"en": "Probe"}
    assert isinstance(built.labels, MappingProxyType)
    assert isinstance(built.parameters, MappingProxyType)
    assert isinstance(STANDARD.labels, MappingProxyType)
    assert isinstance(built.tax_id_schemes[0].labels, MappingProxyType)
    assert isinstance(built.document_types[0].labels, MappingProxyType)
    field = FieldDef(name="ncf", labels=labels, kind=FieldKind.CODE)
    assert isinstance(field.labels, MappingProxyType)
    assert built == regime() == regime(labels=dict(LABELS))
    assert hash(built) == hash(regime())
    assert len({built, regime(), field, FieldDef(name="ncf", labels=labels, kind=FieldKind.CODE)}) == 2


@pytest.mark.parametrize(
    "percent",
    [Decimal(-1), Decimal(101), Decimal("NaN"), Decimal("18.0000001"), 18],
    ids=["negative", "over-a-hundred", "nan", "seven-places", "int"],
)
def test_a_rate_outside_zero_to_a_hundred_percent_or_too_precise_is_a_defect(percent: Decimal) -> None:
    with pytest.raises(InvalidValueError, match=r"^rate\.percent must be"):
        RateValue(percent=percent, source=SOURCE)


@pytest.mark.parametrize("percent", ["0", "100", "18.000001", "1E+1"])
def test_a_rate_of_up_to_six_places_is_accepted(percent: str) -> None:
    assert RateValue(percent=Decimal(percent), source=SOURCE).percent == Decimal(percent)


VALID_RULE: Final[Mapping[str, object]] = {
    "mode": RoundingMode.HALF_UP,
    "points": frozenset({RoundingPoint.LINE}),
    "places": 2,
    "line_tolerance": Decimal("0.01"),
}


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"mode": "half_up"}, "mode"),
        ({"points": {RoundingPoint.LINE}}, "points"),
        ({"points": frozenset({"line"})}, "points"),
        ({"places": -1}, "places"),
        ({"places": MAX_SCALE + 1}, "places"),
        ({"places": True}, "places"),
        ({"line_tolerance": Decimal("-0.01")}, "line_tolerance"),
        ({"line_tolerance": Decimal("Infinity")}, "line_tolerance"),
        ({"line_tolerance": 0.01}, "line_tolerance"),
    ],
)
def test_a_rounding_rule_that_cannot_round_is_a_defect(change: dict[str, object], field: str) -> None:
    # `methodcaller` takes untyped keywords, like a pack built from data the checkers never saw.
    build = operator.methodcaller("__call__", **(dict(VALID_RULE) | change))
    with pytest.raises(InvalidValueError, match=rf"^rounding\.{field} must be"):
        build(RoundingRule)


@pytest.mark.parametrize("places", [0, MAX_SCALE])
def test_a_rounding_rule_rounds_to_any_scale_money_holds(places: int) -> None:
    rule = RoundingRule(mode=RoundingMode.DOWN, points=frozenset(), places=places, line_tolerance=Decimal(0))
    assert rule.places == places


# ── The calculator ────────────────────────────────────────────────────────────────

ROUNDED_LINES: Final = (line("10.03"), line("10.03"), line("10.03"), line("10.04", REDUCED_KEY))


@pytest.mark.parametrize(
    ("points", "tax"),
    [
        (frozenset({RoundingPoint.LINE}), "7.04"),  # 1.8054 → 1.81 three times, 1.6064 → 1.61
        (frozenset({RoundingPoint.CATEGORY}), "7.03"),  # 5.4162 → 5.42, 1.6064 → 1.61
        (frozenset({RoundingPoint.DOCUMENT}), "7.02"),  # 7.0226 → 7.02
        (frozenset(RoundingPoint), "7.04"),  # rounded lines are already at two places
        (frozenset[RoundingPoint](), "7.0226"),  # every digit kept
    ],
    ids=["line", "category", "document", "all", "none"],
)
def test_totals_round_where_the_regime_says(points: frozenset[RoundingPoint], tax: str) -> None:
    totals = TaxCalculator(regime(points=points)).totals(ROUNDED_LINES, DAY)
    assert totals.tax == Money.of(tax, USD)
    assert totals.gross == Money.of("40.13", USD) + Money.of(tax, USD)


def test_each_category_totals_its_base_and_its_tax() -> None:
    totals = TaxCalculator(regime(points=frozenset({RoundingPoint.CATEGORY}))).totals(ROUNDED_LINES, DAY)
    assert totals.categories == (
        CategoryTotal(
            category=REDUCED_KEY, percent=Decimal(16), base=Money.of("10.04", USD), tax=Money.of("1.61", USD)
        ),
        CategoryTotal(
            category=STANDARD_KEY, percent=Decimal(18), base=Money.of("30.09", USD), tax=Money.of("5.42", USD)
        ),
    )


def test_half_even_rounds_a_tie_to_the_even_cent() -> None:
    lines = [line("10.25")]  # 1.845
    assert TaxCalculator(regime(mode=RoundingMode.HALF_EVEN)).totals(lines, DAY).tax == Money.of("1.84", USD)
    assert TaxCalculator(regime(mode=RoundingMode.HALF_UP)).totals(lines, DAY).tax == Money.of("1.85", USD)


def test_a_credit_note_negative_base_rounds_away_from_zero_like_its_invoice() -> None:
    totals = TaxCalculator(regime()).totals([line("-10.05")], DAY)  # -1.809
    assert (totals.net, totals.tax, totals.gross) == (
        Money.of("-10.05", USD),
        Money.of("-1.81", USD),
        Money.of("-11.86", USD),
    )


def test_lines_in_two_currencies_are_refused() -> None:
    with pytest.raises(CurrencyMismatchError):
        TaxCalculator(regime()).totals([line("1"), line("1", REDUCED_KEY, DOP)], DAY)


@pytest.mark.parametrize(
    "points", [frozenset({RoundingPoint.LINE}), frozenset[RoundingPoint]()], ids=["line", "none"]
)
@pytest.mark.parametrize(
    "amount",
    [
        "0.000000000000000000000001",
        "-0.000000000000000000000001",
        "99999999999999999.999999999999999999999999",
    ],
    ids=["smallest", "smallest-negative", "widest"],
)
def test_any_base_money_holds_is_taxed_without_an_error(
    points: frozenset[RoundingPoint], amount: str
) -> None:
    totals = TaxCalculator(regime(points=points)).totals([line(amount)], DAY)
    assert totals.gross == totals.net + totals.tax
    assert abs(totals.tax.amount) <= abs(totals.net.amount)


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(TaxCategoryUnknownError):
        TaxCalculator(regime()).totals([line("1", TaxCategoryKey("us.sales.none"))], DAY)


def test_no_lines_total_nothing_in_the_regime_currency() -> None:
    totals = TaxCalculator(regime()).totals([], DAY)
    assert (totals.net, totals.tax, totals.gross, totals.categories) == (
        Money.zero(USD),
        Money.zero(USD),
        Money.zero(USD),
        (),
    )


amounts = st.decimals(min_value=Decimal(-(10**9)), max_value=Decimal(10**9), places=2).map(
    lambda value: Money(value, USD)
)
precise = st.decimals(min_value=Decimal(-(10**16)), max_value=Decimal(10**16), places=MAX_SCALE).map(
    lambda value: Money(value, USD)
)
categories = st.sampled_from([STANDARD_KEY, REDUCED_KEY])
tax_lines = st.lists(st.builds(TaxLine, base=amounts, category=categories), max_size=30)
precise_lines = st.lists(st.builds(TaxLine, base=precise, category=categories), max_size=5)
rounding_points = st.frozensets(st.sampled_from(list(RoundingPoint)))
rounding_modes = st.sampled_from(list(RoundingMode))


@given(st.one_of(tax_lines, precise_lines), rounding_points, rounding_modes)
def test_the_total_is_the_sum_of_its_parts(
    items: list[TaxLine], rounding: frozenset[RoundingPoint], mode: RoundingMode
) -> None:
    totals = TaxCalculator(regime(points=rounding, mode=mode)).totals(items, DAY)
    assert totals.gross == totals.net + totals.tax
    assert totals.net == sum((total.base for total in totals.categories), start=Money.zero(USD))


@given(tax_lines, rounding_points, st.permutations(range(30)))
def test_the_order_of_the_lines_never_changes_the_totals(
    items: list[TaxLine], rounding: frozenset[RoundingPoint], order: list[int]
) -> None:
    calculator = TaxCalculator(regime(points=rounding))
    shuffled = [items[index] for index in order if index < len(items)]
    assert calculator.totals(items, DAY) == calculator.totals(shuffled, DAY)
