import string
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.regime.definitions import TaxIdCheck
from ledger_country_do.regime.tax_ids import (
    CEDULA,
    MAX_TEXT,
    RNC,
    SCHEMES,
    check_cedula,
    check_rnc,
    normalize,
    scheme_of,
)


@pytest.mark.parametrize("text", ["101850043", "1-01-85004-3", "131098193", " 131098193 ", "401506254"])
def test_a_well_formed_rnc_with_its_check_digit_is_valid(text: str) -> None:
    assert check_rnc(text) is TaxIdCheck.VALID


@pytest.mark.parametrize("text", ["00113918205", "001-1391820-5", "402 1234567 8"])
def test_a_well_formed_cedula_with_its_check_digit_is_valid(text: str) -> None:
    assert check_cedula(text) is TaxIdCheck.VALID


@pytest.mark.parametrize(
    ("check", "text"),
    [(check_rnc, "101850042"), (check_cedula, "00113918204"), (check_cedula, "00112345678")],
)
def test_a_wrong_check_digit_is_reported_not_a_format_error(
    check: Callable[[str], TaxIdCheck], text: str
) -> None:
    assert check(text) is TaxIdCheck.CHECKSUM_MISMATCH


@pytest.mark.parametrize(
    "text",
    [
        "",
        "12345678",
        "1234567890",
        "10185004A",
        "101.850.043",
        "١٠١٨٥٠٠٤٣",
        "1" * (MAX_TEXT + 1),
        "00113918205",
    ],
    ids=["empty", "eight", "ten", "letter", "dots", "arabic-indic", "too-long", "a-cedula"],
)
def test_anything_else_is_not_an_rnc(text: str) -> None:
    assert check_rnc(text) is TaxIdCheck.INVALID_FORMAT


def test_an_rnc_is_not_a_cedula() -> None:
    assert check_cedula("101850043") is TaxIdCheck.INVALID_FORMAT


@given(st.text(alphabet=string.digits, min_size=8, max_size=8))
def test_exactly_one_ninth_digit_completes_any_rnc(first: str) -> None:
    assert sum(check_rnc(first + digit) is TaxIdCheck.VALID for digit in string.digits) == 1


@given(st.text(alphabet=string.digits, min_size=10, max_size=10))
def test_exactly_one_eleventh_digit_completes_any_cedula(first: str) -> None:
    assert sum(check_cedula(first + digit) is TaxIdCheck.VALID for digit in string.digits) == 1


def test_separators_are_ignored_and_nothing_else() -> None:
    assert normalize("1-01 85004-3") == "101850043"
    assert normalize("1.01") is None
    assert normalize("") is None
    assert normalize("1" * (MAX_TEXT + 1)) is None


def test_the_digit_count_names_the_scheme() -> None:
    assert scheme_of("101850043") is RNC
    assert scheme_of("001-1391820-5") is CEDULA
    assert scheme_of("12") is None
    assert scheme_of("abc") is None


def test_the_regime_reports_check_digits_without_enforcing_them() -> None:
    assert [scheme.key for scheme in SCHEMES] == ["do.rnc", "do.cedula"]
    assert not any(scheme.check_digit_enforced for scheme in SCHEMES)
    assert all(scheme.source.url.startswith("https://") for scheme in SCHEMES)
