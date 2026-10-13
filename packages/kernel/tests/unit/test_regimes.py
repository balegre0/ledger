from datetime import date
from decimal import Decimal
from typing import Final, TypedDict, Unpack
lazy from collections.abc import Callable, Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, Money, RoundingMode
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
    TaxCategoryKind,
    TaxCategoryUnknownError,
    TaxIdSchemeDef,
    TaxIdSchemes,
    TaxIdSchemeUnknownError,
    TaxLine,
)
from ledger_kernel.tax_ids import InvalidTaxIdError, TaxId, TaxIdCheck, TaxIdScheme

SOURCE: Final = Source(
    title="Probe tax law", url="https://law.invalid/probe", version="1", retrieved_on=date(2026, 10, 13)
)
USD: Final = Currency.of("USD")
LABELS: Final[Mapping[Locale, str]] = {Locale("en"): "Probe"}
DAY: Final = date(2026, 1, 1)


def digits_check(value: str) -> TaxIdCheck:
    if len(value) != 4 or not value.isascii() or not value.isdigit():
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if sum(map(int, value)) % 2 == 0 else TaxIdCheck.CHECKSUM_MISMATCH


def without_dashes(text: str) -> str:
    return text.replace("-", "")


def scheme(*, enforced: bool, key: str = "us.probe", country: str = "US") -> TaxIdSchemeDef:
    return TaxIdSchemeDef(
        key=TaxIdScheme(key),
        country=CountryCode(country),
        labels=LABELS,
        normalize=without_dashes,
        check=digits_check,
        check_digit_enforced=enforced,
    )


def category(key: str, percent: str) -> TaxCategoryDef:
    return TaxCategoryDef(
        key=key,
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
    key: str
    time_zone: str
    labels: Mapping[Locale, str]
    sources: tuple[Source, ...]
    tax_id_schemes: tuple[TaxIdSchemeDef, ...]
    tax_categories: tuple[TaxCategoryDef, ...]
    document_types: tuple[DocumentTypeDef, ...]
    fields: tuple[FieldDef, ...]


def regime(**changes: Unpack[Changes]) -> RegimeDef:
    points = changes.get("points", frozenset({RoundingPoint.LINE}))
    return RegimeDef(
        key=RegimeKey(changes.get("key", "us")),
        jurisdiction=Jurisdiction(CountryCode("US")),
        currency=USD,
        time_zone=changes.get("time_zone", "America/New_York"),
        labels=changes.get("labels", LABELS),
        tax_id_schemes=changes.get("tax_id_schemes", (scheme(enforced=False),)),
        tax_categories=changes.get("tax_categories", (STANDARD, REDUCED)),
        document_types=changes.get("document_types", (document("1"), document("2", corrects=("1",)))),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP, points=points, places=2, line_tolerance=Decimal("0.01")
        ),
        fields=changes.get("fields", ()),
        parameters={"threshold": Timeline(Since(date(2020, 1, 1), Decimal(1000)))},
        sources=changes.get("sources", (SOURCE,)),
    )


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


def test_a_value_that_is_not_text_is_refused_as_a_bad_format() -> None:
    schemes = TaxIdSchemes((scheme(enforced=False),))
    with pytest.raises(InvalidTaxIdError, match="invalid_format"):
        schemes.parse("us.probe", 1234)  # the boundary hands over whatever arrived


def test_two_regimes_declaring_one_scheme_are_refused() -> None:
    with pytest.raises(InvalidRegimeError, match=r"^regime 'us' declares tax id scheme 'us\.probe' twice$"):
        TaxIdSchemes((scheme(enforced=False), scheme(enforced=True)))


@pytest.mark.parametrize(
    ("built", "problem"),
    [
        (lambda: regime(key="usa"), "has a key that is not"),
        (lambda: regime(key="mx"), "is keyed outside its jurisdiction"),
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


def test_a_regime_of_a_subdivision_is_keyed_under_its_country() -> None:
    assert regime(key="us-ca", tax_categories=(category("us-ca.sales.standard", "7.25"),)).key == "us-ca"


@pytest.mark.parametrize("percent", [Decimal(-1), Decimal(101), Decimal("NaN"), 18])
def test_a_rate_outside_zero_to_a_hundred_percent_is_a_defect(percent: Decimal) -> None:
    with pytest.raises(InvalidValueError):
        RateValue(percent=percent, source=SOURCE)


def test_totals_round_where_the_regime_says() -> None:
    lines = [TaxLine(base=Money.of("10.05", USD), category="us.sales.standard")] * 3
    by_line = TaxCalculator(regime(points=frozenset({RoundingPoint.LINE}))).totals(lines, date(2026, 11, 2))
    by_document = TaxCalculator(regime(points=frozenset({RoundingPoint.DOCUMENT}))).totals(
        lines, date(2026, 11, 2)
    )
    by_category = TaxCalculator(regime(points=frozenset({RoundingPoint.CATEGORY}))).totals(
        lines, date(2026, 11, 2)
    )
    assert by_line.tax == Money.of("5.43", USD)  # 1.809 → 1.81, three times
    assert by_document.tax == Money.of("5.43", USD)  # 5.427 → 5.43
    assert by_category.tax == Money.of("5.43", USD)  # 5.427 → 5.43, once per category
    assert by_line.categories == (
        CategoryTotal(
            category="us.sales.standard",
            percent=Decimal(18),
            base=Money.of("30.15", USD),
            tax=Money.of("5.43", USD),
        ),
    )


def test_unrounded_totals_keep_every_digit() -> None:
    lines = [TaxLine(base=Money.of("10.05", USD), category="us.sales.standard")] * 3
    totals = TaxCalculator(regime(points=frozenset())).totals(lines, DAY)
    assert totals.tax == Money.of("5.427", USD)


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(TaxCategoryUnknownError):
        TaxCalculator(regime()).totals([TaxLine(base=Money.of("1", USD), category="us.sales.none")], DAY)


def test_no_lines_total_nothing_in_the_regime_currency() -> None:
    totals = TaxCalculator(regime()).totals([], DAY)
    assert (totals.net, totals.tax, totals.gross, totals.categories) == (
        Money.zero(USD),
        Money.zero(USD),
        Money.zero(USD),
        (),
    )


amounts = st.decimals(min_value=Decimal(0), max_value=Decimal(10**9), places=2).map(
    lambda value: Money(value, USD)
)
tax_lines = st.lists(
    st.builds(TaxLine, base=amounts, category=st.sampled_from(["us.sales.standard", "us.sales.reduced"])),
    max_size=30,
)
rounding_points = st.frozensets(st.sampled_from(list(RoundingPoint)))


@given(tax_lines, rounding_points)
def test_the_total_is_the_sum_of_its_parts(items: list[TaxLine], rounding: frozenset[RoundingPoint]) -> None:
    totals = TaxCalculator(regime(points=rounding)).totals(items, DAY)
    assert totals.gross == totals.net + totals.tax
    assert totals.net == sum((total.base for total in totals.categories), start=Money.zero(USD))


@given(tax_lines, rounding_points, st.permutations(range(30)))
def test_the_order_of_the_lines_never_changes_the_totals(
    items: list[TaxLine], rounding: frozenset[RoundingPoint], order: list[int]
) -> None:
    calculator = TaxCalculator(regime(points=rounding))
    shuffled = [items[index] for index in order if index < len(items)]
    assert calculator.totals(items, DAY) == calculator.totals(shuffled, DAY)
