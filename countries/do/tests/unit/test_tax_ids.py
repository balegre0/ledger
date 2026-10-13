import string
from contextlib import suppress
from typing import Final
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from ledger_country_do.regime import REGIME
from ledger_country_do.regime.tax_ids import (
    CEDULA,
    MAX_TEXT,
    RNC,
    SCHEME_SOURCES,
    SCHEMES,
    check_cedula,
    check_rnc,
    normalize,
    scheme_of,
)
from ledger_kernel.errors import ErrorKind
from ledger_kernel.jurisdiction import CountryCode
from ledger_kernel.regimes import Locale, TaxIdSchemes
from ledger_kernel.tax_ids import InvalidTaxIdError, TaxId, TaxIdCheck, TaxIdScheme

REGISTRY: Final = TaxIdSchemes(REGIME.tax_id_schemes)
JSON: Final = TypeAdapter(object)


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
    assert REGIME.tax_id_schemes == SCHEMES
    assert not any(scheme.check_digit_enforced for scheme in SCHEMES)
    assert all(scheme.country == CountryCode("DO") for scheme in SCHEMES)
    assert all(SCHEME_SOURCES[scheme.key].url.startswith("https://") for scheme in SCHEMES)
    assert (RNC.labels[Locale("es")], CEDULA.labels[Locale("es")]) == ("RNC", "Cédula")
    assert (RNC.peppol_icd, CEDULA.peppol_icd) == (None, None)


# Through the kernel's registry, as a client's `{"scheme": ..., "value": ...}` is read (§7.3, ADR 0006).


@pytest.mark.parametrize(
    ("scheme", "text", "value"),
    [("do.rnc", "1-01-85004-3", "101850043"), ("do.cedula", "001-0000001-1", "00100000011")],
)
def test_hyphens_and_spaces_are_ignored(scheme: str, text: str, value: str) -> None:
    parsed = REGISTRY.parse(scheme, text)
    assert parsed.tax_id == TaxId(TaxIdScheme(scheme), value)


@pytest.mark.parametrize(
    ("scheme", "text", "value"),
    [
        ("do.rnc", "101850043", "101850043"),
        ("do.rnc", "1-01-85004-3", "101850043"),
        ("do.rnc", "131098193", "131098193"),
        ("do.rnc", "401506254", "401506254"),
        ("do.rnc", "000000002", "000000002"),
        ("do.rnc", "000000061", "000000061"),
        ("do.rnc", "000000132", "000000132"),
        ("do.rnc", "000000051", "000000051"),
        ("do.cedula", "001-1391820-5", "00113918205"),
        ("do.cedula", "224 0002211 1", "22400022111"),
        ("do.cedula", "402 1234567 8", "40212345678"),
    ],
    ids=[
        "rnc",
        "rnc-hyphens",
        "rnc-2",
        "rnc-3",
        "rnc-remainder-0",
        "rnc-remainder-1",
        "rnc-remainder-9",
        "rnc-remainder-10",
        "cedula-hyphens",
        "cedula-spaces",
        "cedula-2",
    ],
)
def test_the_known_valid_numbers_are_valid(scheme: str, text: str, value: str) -> None:
    # The four RNCs of zeros pin every fold of `(10 - remainder) % 9 + 1`: remainders 0, 1, 9 and 10.
    parsed = REGISTRY.parse(scheme, text)
    assert (parsed.tax_id, parsed.check) == (TaxId(TaxIdScheme(scheme), value), TaxIdCheck.VALID)


@pytest.mark.parametrize(
    ("scheme", "text"),
    [("do.rnc", "101850042"), ("do.cedula", "00113918204"), ("do.cedula", "00112345678")],
)
def test_a_check_digit_mismatch_is_reported_not_refused(scheme: str, text: str) -> None:
    parsed = REGISTRY.parse(scheme, text)
    assert (parsed.tax_id.value, parsed.check) == (text, TaxIdCheck.CHECKSUM_MISMATCH)


@pytest.mark.parametrize(
    ("scheme", "text"),
    [
        ("do.rnc", "1018500433"),
        ("do.cedula", "101850043"),
        ("do.rnc", "00113918205"),
        ("do.rnc", "١٠١٨٥٠٠٤٣"),
        ("do.rnc", "x" * 41),
        ("do.rnc", ""),
        ("do.rnc", "---"),
        ("do.rnc", "12345678"),
        ("do.rnc", "abcdefghi"),
        ("do.rnc", "101850043\n"),
        ("do.rnc", "1018.50043"),
        ("do.rnc", "\uff11\uff10\uff11\uff18\uff15\uff10\uff10\uff14\uff13"),
        ("do.rnc", "\u00b2" * 9),
        ("do.rnc", "101\t850043"),
        ("do.rnc", "101850043" + " " * 12),
    ],
    ids=[
        "ten-digits",
        "an-rnc-as-cedula",
        "a-cedula-as-rnc",
        "arabic-indic",
        "too-long",
        "empty",
        "separators-only",
        "eight-digits",
        "letters",
        "newline",
        "dot",
        "full-width-digits",
        "superscript-digits",
        "tab",
        "past-twenty-characters",
    ],
)
def test_a_wrong_length_or_anything_but_ascii_digits_is_refused(scheme: str, text: str) -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        REGISTRY.parse(scheme, text)
    assert str(caught.value) == f"not a valid {scheme} identifier: invalid_format"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_tax_id")


def test_the_text_read_is_capped_at_twenty_characters() -> None:
    assert REGISTRY.parse("do.rnc", "101850043" + " " * 11).tax_id.value == "101850043"


@pytest.mark.parametrize(
    "payload",
    ["101850043", "null", "true", "1.5", '["101850043"]', '{"value": "101850043"}', "1" + "0" * 4000],
    ids=["integer", "null", "true", "float", "list", "object", "huge-integer"],
)
def test_a_value_that_is_not_text_is_refused_without_echo(payload: str) -> None:
    # What a JSON body can hold in place of the text a client was to send.
    with pytest.raises(InvalidTaxIdError) as caught:
        REGISTRY.parse("do.rnc", JSON.validate_json(payload))
    assert "101850043" not in str(caught.value)
    assert len(str(caught.value)) < 80


@given(st.text(string.digits + "- ", max_size=20))
def test_separators_are_ignored_and_the_digit_count_decides(text: str) -> None:
    digits = text.replace("-", "").replace(" ", "")
    for scheme, length in (("do.rnc", 9), ("do.cedula", 11)):
        if len(digits) == length:
            assert REGISTRY.parse(scheme, text).tax_id.value == digits
        else:
            with pytest.raises(InvalidTaxIdError):
                REGISTRY.parse(scheme, text)


@given(st.text())
def test_any_text_is_read_or_refused_cleanly(text: str) -> None:
    for scheme in ("do.rnc", "do.cedula"):
        with suppress(InvalidTaxIdError):
            REGISTRY.parse(scheme, text)
