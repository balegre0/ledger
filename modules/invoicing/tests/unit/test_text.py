from typing import Final
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_invoicing.domain.errors import InvalidEmailError, InvalidTextError
from ledger_invoicing.domain.text import (
    MAX_ADDRESS,
    MAX_DESCRIPTION,
    MAX_EXTERNAL_REFERENCE,
    MAX_LEGAL_NAME,
    MAX_REASON,
    parse_address,
    parse_description,
    parse_email,
    parse_external_reference,
    parse_legal_name,
    parse_reason,
)
from ledger_kernel.errors import ErrorKind


def test_text_is_trimmed_its_spaces_folded_and_composed() -> None:
    assert parse_description("  Cafe\u0301   con\tleche ", field="lines.0.description") == "Café con leche"
    assert parse_legal_name(" Acme  SRL ", field="buyer.legal_name") == "Acme SRL"
    assert parse_address("Calle 1,\n Santo Domingo", field="buyer.address") == "Calle 1, Santo Domingo"
    assert parse_reason("  wrong buyer ") == "wrong buyer"


PARSERS: Final[list[tuple[Callable[[str], str], int, str]]] = [
    (
        lambda text: parse_description(text, field="lines.2.description"),
        MAX_DESCRIPTION,
        "lines.2.description",
    ),
    (lambda text: parse_legal_name(text, field="buyer.legal_name"), MAX_LEGAL_NAME, "buyer.legal_name"),
    (lambda text: parse_address(text, field="buyer.address"), MAX_ADDRESS, "buyer.address"),
    (parse_reason, MAX_REASON, "reason"),
]


@pytest.mark.parametrize(("parse", "limit", "field"), PARSERS)
def test_text_is_refused_by_its_field_and_problem_never_its_content(
    parse: Callable[[str], str], limit: int, field: str
) -> None:
    assert parse("x" * limit) == "x" * limit
    for text, problem in (
        ("   ", "is empty"),
        ("y" * (limit + 1), f"is longer than {limit} characters"),
        ("secret\u200bvalue", "holds a control or format character"),
    ):
        with pytest.raises(InvalidTextError) as caught:
            parse(text)
        assert str(caught.value) == f"{field} {problem}"
        assert "secret" not in str(caught.value)
        assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_text")


def test_limits_are_the_plan_s() -> None:
    assert (MAX_DESCRIPTION, MAX_EXTERNAL_REFERENCE, MAX_LEGAL_NAME) == (500, 100, 300)


def test_an_external_reference_is_kept_exactly_as_sent() -> None:
    assert parse_external_reference("ORD-2026/0001 ñ") == "ORD-2026/0001 ñ"
    assert parse_external_reference("x" * MAX_EXTERNAL_REFERENCE) == "x" * MAX_EXTERNAL_REFERENCE


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "is not 1 to 100 characters"),
        ("x" * 101, "is not 1 to 100 characters"),
        (" ORD-1", "starts or ends with white space"),
        ("ORD-1\n", "starts or ends with white space"),
        ("ORD\x001", "holds a control or format character"),
        ("ORD-Cafe\N{COMBINING ACUTE ACCENT}", "is not in Unicode NFC"),
    ],
)
def test_an_external_reference_that_two_clients_could_read_alike_is_refused(text: str, problem: str) -> None:
    with pytest.raises(InvalidTextError, match=rf"^external_reference {problem}$"):
        parse_external_reference(text)


def test_an_email_is_kept_as_sent() -> None:
    assert parse_email("ventas@acme.com.do", field="buyer.email") == "ventas@acme.com.do"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "acme.com",
        "a@b",
        "a@@b.com",
        "a b@c.com",
        "a@b.com\u200b",
        "@b.com",
        "a@.com",
        "a" * 250 + "@b.com",
    ],
)
def test_what_is_not_an_email_address_is_refused_without_being_repeated(text: str) -> None:
    with pytest.raises(InvalidEmailError) as caught:
        parse_email(text, field="buyer.email")
    assert str(caught.value) == "buyer.email is not an email address of at most 254 characters"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_email")


@given(st.text(max_size=600))
def test_whatever_a_client_types_is_either_clean_text_or_refused(text: str) -> None:
    try:
        cleaned = parse_description(text, field="lines.0.description")
    except InvalidTextError:
        return
    assert cleaned == cleaned.strip()
    assert 1 <= len(cleaned) <= MAX_DESCRIPTION
    assert "  " not in cleaned
    assert parse_description(cleaned, field="lines.0.description") == cleaned
