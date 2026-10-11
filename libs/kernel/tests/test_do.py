import json
import operator
import string
from contextlib import suppress
from dataclasses import FrozenInstanceError
from typing import Final
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.countries.do import InvalidTaxIdError, TaxId, TaxIdKind
from ledger_kernel.errors import ErrorKind

# What `json.loads` can hand over in place of the text or the kind a signature promises.
NOT_TEXT: Final = ["101850043", "null", "true", "1.5", '["101850043"]', '{"digits": "101850043"}']
NOT_TEXT_IDS: Final = ["integer", "null", "true", "float", "list", "object"]


@pytest.mark.parametrize(
    ("text", "digits", "kind"),
    [
        ("101850043", "101850043", TaxIdKind.RNC),
        ("1-01-85004-3", "101850043", TaxIdKind.RNC),
        ("131098193", "131098193", TaxIdKind.RNC),
        ("001-1391820-5", "00113918205", TaxIdKind.CEDULA),
        ("224 0002211 1", "22400022111", TaxIdKind.CEDULA),
    ],
)
def test_rnc_and_cedula_are_read_without_separators(text: str, digits: str, kind: TaxIdKind) -> None:
    tax_id = TaxId.parse(text)
    assert (tax_id.digits, tax_id.kind, str(tax_id)) == (digits, kind, digits)
    assert tax_id.check_digit_matches


@pytest.mark.parametrize("text", ["101850042", "00113918204"])
def test_a_wrong_check_digit_is_reported_not_refused(text: str) -> None:
    assert not TaxId.parse(text).check_digit_matches


@pytest.mark.parametrize(
    "digits",
    [
        pytest.param("000000002", id="remainder-0"),
        pytest.param("000000061", id="remainder-1"),
        pytest.param("000000132", id="remainder-9"),
        pytest.param("000000051", id="remainder-10"),
    ],
)
def test_every_fold_of_the_rnc_remainder_is_pinned(digits: str) -> None:
    # The weighted sums of the first eight digits are 0, 12, 9 and 10, so the remainders modulo 11 are 0, 1, 9
    # and 10: the four where `(10 - remainder) % 9 + 1` wraps around instead of just subtracting.
    assert TaxId.parse(digits).check_digit_matches


def is_built(kind: TaxIdKind, digits: str) -> bool:
    try:
        TaxId(digits, kind)
    except InvalidTaxIdError:
        return False
    return True


@pytest.mark.parametrize("kind", TaxIdKind)
def test_every_kind_has_a_length_and_a_check_digit_rule(kind: TaxIdKind) -> None:
    lengths = [length for length in range(1, 30) if is_built(kind, "1" * length)]
    assert len(lengths) == 1
    first_digits = "1" * (lengths[0] - 1)
    completing = [digit for digit in string.digits if TaxId(first_digits + digit, kind).check_digit_matches]
    assert len(completing) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "12345678",
        "1234567890",
        "123456789012",
        "abcdefghi",
        "١٠١٨٥٠٠٤٣",
        "101850043\n",
        "1018.50043",
        "\uff11\uff10\uff11\uff18\uff15\uff10\uff10\uff14\uff13",
        "\u00b2" * 9,
        "101\t850043",
    ],
    ids=[
        "empty",
        "8-digits",
        "10-digits",
        "12-digits",
        "letters",
        "arabic-digits",
        "newline",
        "dot",
        "full-width-digits",
        "superscript-digits",
        "tab",
    ],
)
def test_anything_else_is_refused(text: str) -> None:
    with pytest.raises(InvalidTaxIdError, match="not an RNC or cédula"):
        TaxId.parse(text)


def test_a_huge_input_is_refused_without_echo() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        TaxId.parse("1" * 100_000)
    assert len(str(caught.value)) < 80


def test_the_text_read_is_capped_at_twenty_characters() -> None:
    assert TaxId.parse("101850043" + " " * 11).digits == "101850043"
    with pytest.raises(InvalidTaxIdError):
        TaxId.parse("101850043" + " " * 12)


def test_a_refusal_is_a_short_client_error() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        TaxId.parse("12345678")
    assert str(caught.value) == "not an RNC or cédula: '12345678'"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_tax_id")


@pytest.mark.parametrize(("digits", "kind"), [("123", TaxIdKind.RNC), ("101850043", TaxIdKind.CEDULA)])
def test_a_tax_id_cannot_be_built_inconsistent(digits: str, kind: TaxIdKind) -> None:
    with pytest.raises(InvalidTaxIdError):
        TaxId(digits, kind)


@pytest.mark.parametrize(
    ("digits", "kind"),
    [
        ("10185004a", TaxIdKind.RNC),
        ("١٠١٨٥٠٠٤٣", TaxIdKind.RNC),
        ("1-01-85004-3", TaxIdKind.RNC),
        ("", TaxIdKind.RNC),
    ],
    ids=["letter", "arabic-digits", "separators", "empty"],
)
def test_a_tax_id_holds_nothing_but_ascii_digits(digits: str, kind: TaxIdKind) -> None:
    with pytest.raises(InvalidTaxIdError, match="not an RNC or cédula"):
        TaxId(digits, kind)


def test_a_huge_digit_string_is_refused_without_echo() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        TaxId("1" * 100_000, TaxIdKind.RNC)
    assert len(str(caught.value)) < 80


# The entry points take the JSON a client sent: `json.loads` hands over whatever was sent, whatever the
# signature promises, and `methodcaller` takes untyped arguments, so neither type checker objects to it.
def construct(payload: str) -> None:
    operator.methodcaller("__call__", json.loads(payload), TaxIdKind.RNC)(TaxId)


def parse(payload: str) -> None:
    operator.methodcaller("parse", json.loads(payload))(TaxId)


def construct_with_kind(payload: str) -> None:
    operator.methodcaller("__call__", "101850043", json.loads(payload))(TaxId)


@pytest.mark.parametrize("payload", NOT_TEXT, ids=NOT_TEXT_IDS)
@pytest.mark.parametrize("entry_point", [construct, parse], ids=["constructor", "parse"])
def test_the_text_must_be_text_on_every_way_in(entry_point: Callable[[str], None], payload: str) -> None:
    with pytest.raises(InvalidTaxIdError, match="not an RNC or cédula"):
        entry_point(payload)


@pytest.mark.parametrize(
    ("entry_point", "field"), [(construct, "digits"), (parse, "text")], ids=["constructor", "parse"]
)
def test_a_wrong_type_is_named_with_its_argument_and_not_blamed_on_the_digits(
    entry_point: Callable[[str], None], field: str
) -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        entry_point("101850043")
    assert str(caught.value) == f"not an RNC or cédula: {field}='int: 101850043'"


@pytest.mark.parametrize(
    ("digits", "payload"),
    [
        ("101850043", '"rnc"'),
        ("00113918205", '"cedula"'),
        ("101850043", '"RNC"'),
        ("101850043", "null"),
        ("101850043", "1"),
        ("101850043", "true"),
        ("101850043", "[]"),
    ],
    ids=["rnc-as-text", "cedula-as-text", "unknown-text", "null", "number", "true", "list"],
)
def test_the_kind_must_be_a_tax_id_kind(digits: str, payload: str) -> None:
    # A plain "rnc" equals `TaxIdKind.RNC` and hashes like it, so only an exact type check refuses it.
    build = operator.methodcaller("__call__", digits, json.loads(payload))
    with pytest.raises(InvalidTaxIdError, match="not an RNC or cédula"):
        build(TaxId)


def test_a_wrong_kind_is_named_as_the_kind_and_not_blamed_on_the_digits() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        construct_with_kind('"rnc"')
    assert str(caught.value) == "not an RNC or cédula: kind='str: rnc'"
    assert caught.value.code == "invalid_tax_id"


def test_a_wrong_kind_is_a_short_client_error() -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        construct_with_kind(json.dumps("X" * 10_000))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_tax_id")
    assert len(str(caught.value)) < 80


@pytest.mark.parametrize("entry_point", [construct, parse], ids=["constructor", "parse"])
def test_a_huge_wrong_type_is_refused_without_echo(entry_point: Callable[[str], None]) -> None:
    with pytest.raises(InvalidTaxIdError) as caught:
        entry_point("[" + "1," * 100_000 + "1]")
    assert len(str(caught.value)) < 100


def test_a_number_too_big_to_print_is_refused_not_raised() -> None:
    # `str(10**5000)` raises ValueError, so describing a value must not print it whole.
    build = operator.methodcaller("__call__", 10**5000, TaxIdKind.RNC)
    with pytest.raises(InvalidTaxIdError) as caught:
        build(TaxId)
    assert len(str(caught.value)) < 100


def test_a_parsed_tax_id_holds_exactly_the_types_it_promises() -> None:
    tax_id = TaxId.parse("1-01-85004-3")
    assert (type(tax_id.digits), type(tax_id.kind)) == (str, TaxIdKind)


def test_a_tax_id_is_an_immutable_value() -> None:
    tax_id = TaxId.parse("1-01-85004-3")
    assert tax_id == TaxId("101850043", TaxIdKind.RNC)
    assert len({tax_id, TaxId.parse("101850043")}) == 1
    assert TaxId("101850043", TaxIdKind.RNC) != TaxId.parse("00113918205")
    field = "digits"
    with pytest.raises(FrozenInstanceError):
        setattr(tax_id, field, "131098193")


@given(st.text(string.digits, min_size=8, max_size=8))
def test_exactly_one_last_digit_completes_an_rnc(first_eight: str) -> None:
    completing = [
        digit for digit in string.digits if TaxId(first_eight + digit, TaxIdKind.RNC).check_digit_matches
    ]
    assert len(completing) == 1
    assert completing != ["0"]  # the check digit runs from 1 to 9


@given(st.text(string.digits, min_size=10, max_size=10))
def test_exactly_one_last_digit_completes_a_cedula(first_ten: str) -> None:
    completing = [
        digit for digit in string.digits if TaxId(first_ten + digit, TaxIdKind.CEDULA).check_digit_matches
    ]
    assert len(completing) == 1


@given(st.text(string.digits + "- ", max_size=20))
def test_separators_are_ignored_and_the_digit_count_picks_the_kind(text: str) -> None:
    digits = text.replace("-", "").replace(" ", "")
    expected = {9: TaxIdKind.RNC, 11: TaxIdKind.CEDULA}.get(len(digits))
    if expected is None:
        with pytest.raises(InvalidTaxIdError):
            TaxId.parse(text)
    else:
        assert TaxId.parse(text) == TaxId(digits, expected)


@given(st.text())
def test_any_text_is_read_or_refused_cleanly(text: str) -> None:
    with suppress(InvalidTaxIdError):
        TaxId.parse(text)
