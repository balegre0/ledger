from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    InvalidTextLimitError,
    LedgerError,
    NotFoundError,
    UnavailableError,
    described,
    excerpt,
    truncate,
)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (LedgerError, ErrorKind.INTERNAL, "internal"),
        (InvalidInputError, ErrorKind.INVALID, "invalid_input"),
        (NotFoundError, ErrorKind.NOT_FOUND, "not_found"),
        (ConflictError, ErrorKind.CONFLICT, "conflict"),
        (ForbiddenError, ErrorKind.FORBIDDEN, "forbidden"),
        (UnavailableError, ErrorKind.UNAVAILABLE, "unavailable"),
    ],
)
def test_every_base_error_has_a_kind_and_a_stable_code(
    error: type[LedgerError], kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)


class DuplicateInvoiceError(ConflictError):
    code: ClassVar[str] = "duplicate_invoice"

    def __init__(self, number: int) -> None:
        super().__init__(f"invoice {number} exists")


def test_a_subclass_refines_the_code_and_keeps_the_kind() -> None:
    error = DuplicateInvoiceError(7)
    assert (error.kind, error.code, str(error)) == (
        ErrorKind.CONFLICT,
        "duplicate_invoice",
        "invoice 7 exists",
    )


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("", 1, ""),
        ("a", 1, "a"),
        ("ab", 1, "a..."),
        ("abc", 3, "abc"),
        ("abcd", 3, "abc..."),
        ("abc", 10, "abc"),
        ("x" * 1_000_000, 5, "xxxxx..."),
    ],
    ids=["empty", "at-the-limit-1", "one-over", "at-the-limit-3", "cut", "under", "huge"],
)
def test_a_text_is_cut_at_the_limit_and_the_cut_is_marked(text: str, limit: int, expected: str) -> None:
    assert truncate(text, limit) == expected


@given(text=st.text(), limit=st.integers(min_value=1, max_value=100))
def test_a_truncated_text_is_the_text_or_its_prefix_with_a_mark(text: str, limit: int) -> None:
    cut = truncate(text, limit)
    assert cut == (text if len(text) <= limit else text[:limit] + "...")
    assert len(cut) <= limit + len("...")


@pytest.mark.parametrize("limit", [0, -1, -40])
def test_truncating_to_less_than_one_character_is_a_programming_error(limit: int) -> None:
    with pytest.raises(InvalidTextLimitError, match=f"at least 1, not {limit}"):
        truncate("abc", limit)


def test_an_excerpt_is_short_and_quoted() -> None:
    assert excerpt("abc") == "'abc'"
    assert excerpt("x" * 10_000) == "'" + "x" * 40 + "...'"
    assert excerpt("a\nb") == "'a\\nb'"


def test_a_text_of_exactly_the_limit_is_not_cut() -> None:
    assert excerpt("x" * 40) == "'" + "x" * 40 + "'"
    assert excerpt("x" * 41) == "'" + "x" * 40 + "...'"
    assert excerpt("abc", limit=3) == "'abc'"


def test_the_limit_can_be_chosen() -> None:
    assert excerpt("abcdef", limit=3) == "'abc...'"
    assert excerpt("abc", limit=1) == "'a...'"
    assert excerpt("a", limit=1) == "'a'"


@pytest.mark.parametrize("limit", [0, -1, -40])
@pytest.mark.parametrize("text", ["", "abc"], ids=["empty", "short"])
def test_a_limit_below_one_is_a_programming_error(text: str, limit: int) -> None:
    with pytest.raises(InvalidTextLimitError, match=f"at least 1, not {limit}") as caught:
        excerpt(text, limit=limit)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "invalid_text_limit")
    assert not isinstance(caught.value, InvalidInputError)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12.5, "float: 12.5"),
        (7, "int: 7"),
        (True, "bool: True"),
        (None, "NoneType: None"),
        ("abc", "str: abc"),
        ("", "str: "),
        ([1, "a"], "list: [1, 'a']"),
        ({"a": 1}, "dict: {'a': 1}"),
        (ErrorKind.INVALID, "ErrorKind: invalid"),
    ],
    ids=["float", "integer", "boolean", "none", "text", "empty-text", "list", "dict", "str-enum"],
)
def test_a_value_is_described_led_by_its_type(value: object, expected: str) -> None:
    assert described(value) == expected


def test_a_number_is_not_mistaken_for_the_text_of_its_digits() -> None:
    assert described(101850043) == "int: 101850043"
    assert described("101850043") == "str: 101850043"


def test_the_type_survives_an_excerpt() -> None:
    assert excerpt(described(12.5)) == "'float: 12.5'"
    assert excerpt(described("x" * 10_000)) == "'str: " + "x" * 35 + "...'"


def test_a_long_text_is_cut_like_an_excerpt() -> None:
    assert described("x" * 40) == "str: " + "x" * 40
    assert described("x" * 41) == "str: " + "x" * 40 + "..."
    assert described("x" * 1_000_000) == "str: " + "x" * 40 + "..."


@pytest.mark.parametrize(
    "value",
    [
        "x" * 1_000_000,
        10**5000,
        list(range(1_000_000)),
        {str(number) * 100: "y" * 1_000 for number in range(100)},
        [[["z" * 1_000] * 100] * 100] * 100,
        b"b" * 1_000_000,
    ],
    ids=["text", "integer-past-the-str-limit", "list", "dict", "nested", "bytes"],
)
def test_a_description_is_bounded_whatever_the_value(value: object) -> None:
    assert len(described(value)) < 400


def test_an_integer_too_big_to_print_is_described_not_raised() -> None:
    # `str(10**5000)` raises ValueError: the description of a value must never fail on the value.
    assert described(10**5000).startswith("int: ")
