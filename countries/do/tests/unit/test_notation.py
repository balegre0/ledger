from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
lazy from collections.abc import Callable

import pytest

from ledger_country_do.channels.ecf_v1.domain.notation import (
    DGII_YEARS,
    SANTO_DOMINGO,
    NaiveMomentError,
    dgii_amount,
    dgii_date,
    dgii_moment,
    dgii_number,
    dgii_percent,
    local_date,
    parse_dgii_date,
    parse_dgii_moment,
    require_aware,
)

D = Decimal
ARABIC_SIX: Final = "٦"
"""A digit, but no ASCII one: DGII's dates are written in ASCII digits."""


def test_a_date_is_day_month_year() -> None:
    assert dgii_date(date(2026, 1, 5)) == "05-01-2026"


def test_a_moment_is_written_in_the_dominican_republic_s_time() -> None:
    assert dgii_moment(datetime(2026, 10, 13, 13, 30, 5, tzinfo=UTC)) == "13-10-2026 09:30:05"
    assert dgii_moment(datetime(2026, 10, 13, 9, 30, tzinfo=SANTO_DOMINGO)) == "13-10-2026 09:30:00"


def test_the_local_date_is_the_dominican_republic_s() -> None:
    assert local_date(datetime(2026, 10, 14, 2, 0, tzinfo=UTC)) == date(2026, 10, 13)


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (D("750.5212"), "750.52"),
        (D("750.5276"), "750.53"),
        (D("0.005"), "0.01"),
        (D(1000), "1000.00"),
        (D("1E+3"), "1000.00"),
    ],
)
def test_an_amount_has_two_decimals_rounded_half_up(value: Decimal, written: str) -> None:
    assert dgii_amount(value) == written


def test_a_quantity_or_price_is_written_as_it_is_never_in_exponent_form() -> None:
    assert dgii_number(D("120.3333")) == "120.3333"
    assert dgii_number(D("1E+2")) == "100"


@pytest.mark.parametrize(
    ("value", "written"), [(D(18), "18"), (D("16.00"), "16"), (D(0), "0"), (D("1E+1"), "10")]
)
def test_a_rate_is_an_integer(value: Decimal, written: str) -> None:
    assert dgii_percent(value) == written


@pytest.mark.parametrize("read", [dgii_moment, local_date, require_aware])
def test_a_naive_moment_is_refused_never_read_in_the_machine_s_time(
    read: Callable[[datetime], object],
) -> None:
    with pytest.raises(NaiveMomentError) as caught:
        read(datetime(2026, 10, 13, 9, 30, tzinfo=UTC).replace(tzinfo=None))
    assert caught.value.code == "do_ecf_naive_moment"


def test_an_aware_moment_is_kept_as_it_is() -> None:
    moment = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
    assert require_aware(moment) is moment


def test_a_dgii_moment_reads_back_in_the_dominican_republic_s_time() -> None:
    moment = datetime(2026, 10, 13, 13, 30, 5, tzinfo=UTC)
    assert parse_dgii_moment(dgii_moment(moment)) == moment
    assert parse_dgii_moment(" 13-10-2026 09:30:05 ") == moment
    found = parse_dgii_moment("13-10-2026 09:30:05")
    assert found is not None
    assert found.tzinfo is SANTO_DOMINGO


@pytest.mark.parametrize(
    "text",
    [
        "31-02-2026 10:00:00",
        "2026-10-13T09:30:05",
        "13-10-2026",
        "",
        "ayer",
        "31-12-9999 23:59:59",
        "01-01-0001 00:00:00",
        "31-12-1999 23:59:59",
        f"13-10-2026 09:30:0{ARABIC_SIX}",
    ],
)
def test_what_is_no_dgii_moment_reads_as_none(text: str) -> None:
    assert parse_dgii_moment(text) is None


def test_dgii_s_years_are_this_century_s() -> None:
    assert (DGII_YEARS.start, DGII_YEARS.stop) == (2000, 2100)
    assert parse_dgii_date("01-01-2000") == date(2000, 1, 1)
    assert parse_dgii_moment("31-12-2099 23:59:59") is not None


def test_a_dgii_date_reads_back() -> None:
    assert parse_dgii_date(dgii_date(date(2026, 1, 5))) == date(2026, 1, 5)
    assert parse_dgii_date(" 5-1-2026 ") == date(2026, 1, 5)


@pytest.mark.parametrize(
    "text",
    [
        "31-02-2026",
        "2026-10-13",
        "13-10-2026 09:30:05",
        "",
        "hoy",
        "31-12-1999",
        "01-01-2100",
        "31-12-9999",
        "01-01-0001",
        f"13-10-202{ARABIC_SIX}",
    ],
)
def test_what_is_no_dgii_date_reads_as_none(text: str) -> None:
    assert parse_dgii_date(text) is None
