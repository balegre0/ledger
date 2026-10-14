import decimal
import tomllib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.regime import REGIME
from ledger_country_do.regime.document_types import TYPES_SOURCE
from ledger_country_do.regime.entries import (
    EntryKind,
    RegimeEntryError,
    amount,
    category,
    document_type,
    parameter,
    rate,
)
from ledger_country_do.regime.parameters import PARAMETER_SOURCES, PARAMETERS, RFCE_THRESHOLD
from ledger_country_do.regime.rounding import (
    DECIMAL_ROUNDING,
    GLOBAL_TOLERANCE_PER_LINE,
    ROUNDING,
    ROUNDING_SOURCE,
    UNIT_PRICE_PLACES,
    round_amount,
    within_global_tolerance,
    within_line_tolerance,
)
from ledger_country_do.regime.sources import COVERED_FROM
from ledger_country_do.regime.tax_ids import SCHEME_SOURCES
from ledger_kernel.dated import InvalidTimelineError, NotInEffectError, Since, Timeline
from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import (
    Direction,
    DocumentKind,
    InvalidRegimeError,
    Locale,
    RegimeDef,
    RoundingPoint,
    TaxCategoryKind,
)
from ledger_platform.config.manifest import PackageManifest
from ledger_platform.runtime.registries import Regimes

TODAY: Final = date(2026, 10, 13)
PACKAGE: Final = Path(__file__).resolve().parents[2]
SPANISH: Final = Locale("es")


def test_the_regime_is_the_dominican_republic() -> None:
    assert REGIME.key == "do"
    assert REGIME.jurisdiction == Jurisdiction(CountryCode("DO"))
    assert REGIME.currency == Currency.of("DOP")
    assert REGIME.time_zone == "America/Santo_Domingo"
    assert REGIME.labels == {Locale("es"): "República Dominicana", Locale("en"): "Dominican Republic"}
    assert REGIME.fields == ()


def test_the_installed_entry_point_registers_the_regime() -> None:
    regimes = Regimes.load()
    assert regimes.get("do") is REGIME
    assert regimes.tax_id_schemes.parse("do.rnc", "1-01-85004-3").tax_id.value == "101850043"


def test_the_manifest_names_the_regime_the_entry_point_holds() -> None:
    manifest = PackageManifest.model_validate(
        tomllib.loads((PACKAGE / "manifest.toml").read_text(encoding="utf-8"))
    )
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    (entry,) = manifest.regimes
    assert (entry.key, entry.entry_point) == ("do", "ledger_country_do.regime:REGIME")
    assert project["project"]["entry-points"]["ledger.countries.regimes"] == {"do": entry.entry_point}
    assert manifest.distribution == project["project"]["name"] == "ledger-country-do"
    assert manifest.country is not None
    assert (manifest.country.iso, manifest.country.currencies, manifest.country.time_zone) == (
        REGIME.jurisdiction.country,
        (REGIME.currency.code,),
        REGIME.time_zone,
    )


def test_itbis_rates_in_force_today() -> None:
    rates = {key: rate(REGIME, key, TODAY) for key in ("do.itbis.standard", "do.itbis.reduced")}
    assert rates == {"do.itbis.standard": Decimal(18), "do.itbis.reduced": Decimal(16)}
    assert rate(REGIME, "do.itbis.zero", TODAY) == 0


def test_the_exempt_category_charges_nothing_and_says_it_is_exempt() -> None:
    kinds = {each.key: each.kind for each in REGIME.tax_categories}
    assert kinds == {
        "do.itbis.standard": TaxCategoryKind.STANDARD,
        "do.itbis.reduced": TaxCategoryKind.REDUCED,
        "do.itbis.zero": TaxCategoryKind.ZERO,
        "do.itbis.exempt": TaxCategoryKind.EXEMPT,
    }
    assert rate(REGIME, "do.itbis.exempt", TODAY) == 0
    assert category(REGIME, "do.itbis.exempt").labels[SPANISH] == "Exento"


def test_no_rate_is_asserted_before_the_channel_s_era() -> None:
    with pytest.raises(NotInEffectError, match=r"no value is in effect on 2018-12-31"):
        rate(REGIME, "do.itbis.standard", date(2018, 12, 31))


def test_the_ten_e_cf_types_with_their_kind_direction_and_buyer_rule() -> None:
    types = {kind.code: kind for kind in REGIME.document_types}
    assert list(types) == ["31", "32", "33", "34", "41", "43", "44", "45", "46", "47"]
    assert {code for code, kind in types.items() if kind.requires_buyer_tax_id} == {"31", "41", "45"}
    assert {code for code, kind in types.items() if kind.direction is Direction.PURCHASE} == {
        "41",
        "43",
        "47",
    }
    assert (types["33"].kind, types["34"].kind) == (DocumentKind.DEBIT_NOTE, DocumentKind.CREDIT_NOTE)
    assert document_type(REGIME, "32").labels[SPANISH] == "Factura de Consumo Electrónica"
    assert all(kind.corrects == () for kind in REGIME.document_types)


def test_dated_parameters() -> None:
    assert parameter(REGIME, "rfce_threshold", TODAY) == Decimal("250000.00")
    assert amount(REGIME, RFCE_THRESHOLD, TODAY) == Decimal("250000.00")
    assert parameter(REGIME, "contingency_connectivity_hours", TODAY) == 72
    assert parameter(REGIME, "contingency_paper_max_days", TODAY) == 15
    assert parameter(REGIME, "contingency_replacement_days", TODAY) == 30
    assert parameter(REGIME, "legal_name_max", TODAY) == 150
    assert parameter(REGIME, "lines_max", TODAY) == 1000


def test_every_value_cites_a_source_the_regime_lists() -> None:
    assert set(PARAMETERS) == set(PARAMETER_SOURCES)
    cited = {
        *PARAMETER_SOURCES.values(),
        *SCHEME_SOURCES.values(),
        *(entry.value.source for each in REGIME.tax_categories for entry in each.rates.entries),
        ROUNDING_SOURCE,
        TYPES_SOURCE,
    }
    assert cited <= set(REGIME.sources)
    assert set(SCHEME_SOURCES) == {scheme.key for scheme in REGIME.tax_id_schemes}
    assert all(source.url.startswith("https://") for source in REGIME.sources)


@pytest.mark.parametrize(
    ("look_up", "kind", "key"),
    [
        (lambda: category(REGIME, "x"), EntryKind.TAX_CATEGORY, "x"),
        (lambda: document_type(REGIME, "99"), EntryKind.DOCUMENT_TYPE, "99"),
        (lambda: parameter(REGIME, "x", TODAY), EntryKind.PARAMETER, "x"),
    ],
    ids=["category", "document-type", "parameter"],
)
def test_unknown_entries_are_refused_by_name(
    look_up: Callable[[], object], kind: EntryKind, key: str
) -> None:
    with pytest.raises(RegimeEntryError) as caught:
        look_up()
    assert str(caught.value) == f"regime 'do' {kind} {key!r} is not declared"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "do_regime_entry_invalid")


def test_an_amount_parameter_that_is_not_a_decimal_is_a_defect_of_the_pack() -> None:
    broken = replace(REGIME, parameters={RFCE_THRESHOLD: Timeline(Since(COVERED_FROM, 250000))})
    with pytest.raises(RegimeEntryError) as caught:
        amount(broken, RFCE_THRESHOLD, TODAY)
    assert str(caught.value) == "regime 'do' parameter 'rfce_threshold' is not a decimal amount"
    assert isinstance(caught.value, LedgerError)


def test_a_timeline_lists_its_values_newest_first() -> None:
    timeline = Timeline(Since(date(2025, 1, 1), 2), Since(COVERED_FROM, 1))
    assert (timeline.at(date(2024, 12, 31)), timeline.at(date(2025, 1, 1))) == (1, 2)
    with pytest.raises(InvalidTimelineError, match="holds no value"):
        Timeline[int]()
    for wrong in (
        (Since(COVERED_FROM, 1), Since(date(2025, 1, 1), 2)),
        (Since(COVERED_FROM, 1), Since(COVERED_FROM, 2)),
    ):
        with pytest.raises(InvalidTimelineError, match="newest first"):
            Timeline(*wrong)


def test_a_regime_refuses_duplicates_and_a_pack_without_sources() -> None:
    with pytest.raises(InvalidRegimeError, match="declares document type '31' twice"):
        replace(REGIME, document_types=REGIME.document_types * 2)
    with pytest.raises(InvalidRegimeError, match="names no source"):
        replace(REGIME, sources=())


@pytest.mark.parametrize(
    ("value", "rounded"),
    [
        ("750.5212", "750.52"),
        ("750.5276", "750.53"),
        ("750.525", "750.53"),
        ("0.005", "0.01"),
        ("2.675", "2.68"),
    ],
)
def test_amounts_round_half_up_to_two_places(value: str, rounded: str) -> None:
    assert round_amount(Decimal(value)) == Decimal(rounded)


@given(st.decimals(min_value=0, max_value=10**12, places=6, allow_nan=False, allow_infinity=False))
def test_rounding_moves_an_amount_by_half_a_cent_at_most_and_once(value: Decimal) -> None:
    rounded = round_amount(value)
    assert abs(rounded - value) <= Decimal("0.005")
    assert round_amount(rounded) == rounded


def test_the_technical_report_s_tolerance_examples() -> None:
    # IT §12: each line may differ from quantity x price by one unit; the total by as many units as lines.
    assert within_line_tolerance(Decimal("552.80"), quantity=Decimal("5.50"), unit_price=Decimal("100.33"))
    assert within_line_tolerance(Decimal("667.50"), quantity=Decimal(2), unit_price=Decimal("333.25"))
    assert not within_line_tolerance(Decimal("668.51"), quantity=Decimal(2), unit_price=Decimal("333.25"))
    assert within_global_tolerance(Decimal("6914.30"), computed=Decimal("6911.58"), lines=3)
    assert not within_global_tolerance(Decimal("6914.59"), computed=Decimal("6911.58"), lines=3)


def test_the_rounding_rule_is_dgii_s() -> None:
    assert (ROUNDING.mode, ROUNDING.places, ROUNDING.line_tolerance) == (RoundingMode.HALF_UP, 2, Decimal(1))
    assert ROUNDING.points == {RoundingPoint.CATEGORY, RoundingPoint.DOCUMENT}
    assert (4, Decimal(1)) == (UNIT_PRICE_PLACES, GLOBAL_TOLERANCE_PER_LINE)
    assert REGIME.rounding is ROUNDING


def test_every_rounding_mode_rounds_as_decimal_s_constant_of_the_same_name() -> None:
    assert DECIMAL_ROUNDING == {
        RoundingMode.HALF_UP: decimal.ROUND_HALF_UP,
        RoundingMode.HALF_EVEN: decimal.ROUND_HALF_EVEN,
        RoundingMode.DOWN: decimal.ROUND_DOWN,
        RoundingMode.UP: decimal.ROUND_UP,
    }


def test_the_regime_is_a_kernel_regime() -> None:
    assert isinstance(REGIME, RegimeDef)


def test_every_source_says_when_it_was_retrieved_and_none_is_dated_later() -> None:
    assert REGIME.sources
    assert all(date(2019, 1, 1) <= source.retrieved_on <= date(2026, 10, 13) for source in REGIME.sources)
