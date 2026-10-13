from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.regime import REGIME
from ledger_country_do.regime.definitions import (
    Direction,
    DocumentKind,
    NotInForceError,
    RegimeDataError,
    Since,
    Timeline,
    UnknownEntryError,
)
from ledger_country_do.regime.parameters import PARAMETER_SOURCES, PARAMETERS
from ledger_country_do.regime.rounding import round_amount, within_global_tolerance, within_line_tolerance
from ledger_country_do.regime.sources import COVERED_FROM

TODAY = date(2026, 10, 13)


def test_the_regime_is_the_dominican_republic() -> None:
    assert (REGIME.key, REGIME.country, REGIME.currency, REGIME.time_zone) == (
        "do",
        "DO",
        "DOP",
        "America/Santo_Domingo",
    )


def test_itbis_rates_in_force_today() -> None:
    rates = {key: REGIME.category(key).rate_at(TODAY) for key in ("do.itbis.standard", "do.itbis.reduced")}
    assert rates == {"do.itbis.standard": Decimal(18), "do.itbis.reduced": Decimal(16)}
    assert REGIME.category("do.itbis.zero").rate_at(TODAY) == 0
    assert REGIME.category("do.itbis.exempt").rate_at(TODAY) is None


def test_no_rate_is_asserted_before_the_channel_s_era() -> None:
    with pytest.raises(NotInForceError, match=r"do\.itbis\.standard has no value in force on 2018-12-31"):
        REGIME.category("do.itbis.standard").rate_at(date(2018, 12, 31))


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
    assert REGIME.document_type("32").label == "Factura de Consumo Electrónica"


def test_dated_parameters() -> None:
    assert REGIME.parameter("rfce_threshold", TODAY) == Decimal("250000.00")
    assert REGIME.parameter("contingency_connectivity_hours", TODAY) == 72
    assert REGIME.parameter("contingency_paper_max_days", TODAY) == 15
    assert REGIME.parameter("contingency_replacement_days", TODAY) == 30
    assert REGIME.parameter("legal_name_max", TODAY) == 150
    assert REGIME.parameter("lines_max", TODAY) == 1000


def test_every_value_cites_a_source_the_regime_lists() -> None:
    assert set(PARAMETERS) == set(PARAMETER_SOURCES)
    assert set(PARAMETER_SOURCES.values()) <= set(REGIME.sources)
    assert all(source.url.startswith("https://") for source in REGIME.sources)


def test_unknown_entries_are_refused_by_name() -> None:
    with pytest.raises(UnknownEntryError, match="no tax category 'x'"):
        REGIME.category("x")
    with pytest.raises(UnknownEntryError, match="no document type '99'"):
        REGIME.document_type("99")
    with pytest.raises(UnknownEntryError, match="no parameter 'x'"):
        REGIME.parameter("x", TODAY)


def test_a_timeline_lists_its_values_newest_first() -> None:
    timeline = Timeline("t", (Since(date(2025, 1, 1), 2), Since(COVERED_FROM, 1)))
    assert (timeline.at(date(2024, 12, 31)), timeline.at(date(2025, 1, 1))) == (1, 2)
    with pytest.raises(RegimeDataError, match="at least one value"):
        Timeline[int]("t", ())
    for wrong in (
        (Since(COVERED_FROM, 1), Since(date(2025, 1, 1), 2)),
        (Since(COVERED_FROM, 1), Since(COVERED_FROM, 2)),
    ):
        with pytest.raises(RegimeDataError, match="newest first"):
            Timeline("t", wrong)


def test_a_regime_refuses_duplicates_and_uncited_sources() -> None:
    with pytest.raises(RegimeDataError, match="document type is declared twice"):
        replace(REGIME, document_types=REGIME.document_types * 2)
    with pytest.raises(RegimeDataError, match="cites a source"):
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
