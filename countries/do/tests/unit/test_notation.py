from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ledger_country_do.channels.ecf_v1.domain.notation import (
    SANTO_DOMINGO,
    dgii_amount,
    dgii_date,
    dgii_moment,
    dgii_number,
    dgii_percent,
    local_date,
)

D = Decimal


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
