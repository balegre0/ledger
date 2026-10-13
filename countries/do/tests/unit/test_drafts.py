from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
lazy from collections.abc import Callable
lazy from contextlib import AbstractContextManager

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    MAX_LINES,
    BillingIndicator,
    Buyer,
    DraftInvalidError,
    Issuer,
    UnsupportedTypeError,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do_testing.samples import (
    BUYER,
    CREDIT_LINES,
    ISSUER,
    SEQUENCE_EXPIRES_ON,
    consumer_invoice,
    credit_invoice,
)

LINE = CREDIT_LINES[0]


def refused(field: str) -> AbstractContextManager[pytest.ExceptionInfo[DraftInvalidError]]:
    return pytest.raises(DraftInvalidError, match=f"^{field} ")


def test_the_samples_are_drafts_of_the_spike_s_two_types() -> None:
    assert credit_invoice().ecf_type is EcfType.CREDIT_INVOICE
    assert consumer_invoice().ecf_type is EcfType.CONSUMER_INVOICE
    assert consumer_invoice().buyer.anonymous
    assert not BUYER.anonymous


def test_only_types_31_and_32_are_drafted_in_the_spike() -> None:
    with pytest.raises(UnsupportedTypeError, match="e-CF type 33"):
        replace(credit_invoice(), encf=Encf(EcfType.DEBIT_NOTE, 1))


@pytest.mark.parametrize("count", [0, MAX_LINES + 1])
def test_a_draft_holds_one_to_a_thousand_lines(count: int) -> None:
    with refused("lines"):
        replace(credit_invoice(), lines=(LINE,) * count)


def test_the_sequence_s_expiry_is_given_exactly_for_dated_types() -> None:
    with refused("sequence_expires_on"):
        replace(credit_invoice(), sequence_expires_on=None)
    with refused("sequence_expires_on"):
        replace(consumer_invoice(), sequence_expires_on=SEQUENCE_EXPIRES_ON)


def test_a_credit_invoice_names_its_buyer() -> None:
    with refused("buyer"):
        replace(credit_invoice(), buyer=Buyer(name="SOMEONE"))
    with refused("buyer"):
        replace(credit_invoice(), buyer=Buyer(rnc="131098193"))


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("issuer.rnc", lambda: replace(ISSUER, rnc="1-01-85004-3")),
        ("issuer.rnc", lambda: replace(ISSUER, rnc="10185004")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name=" ")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name="X" * 151)),
        ("issuer.address", lambda: replace(ISSUER, address="X" * 101)),
        ("buyer.rnc", lambda: Buyer(rnc="ABC")),
        ("buyer.name", lambda: Buyer(name="X" * 151)),
        ("line.name", lambda: replace(LINE, name="X" * 81)),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal(0))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("1.005"))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("NaN"))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal(-1))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal("1.00001"))),
        ("line.billing", lambda: replace(LINE, billing=BillingIndicator.NOT_BILLABLE)),
        (
            "line.quantity",
            lambda: replace(LINE, quantity=Decimal(10000000000000000), unit_price=Decimal(0)),
        ),
        ("line.unit_price", lambda: replace(LINE, quantity=Decimal("0.01"), unit_price=Decimal("1E16"))),
        ("line.amount", lambda: replace(LINE, quantity=Decimal(2), unit_price=Decimal(5000000000000000))),
        (
            "line.amount",
            lambda: replace(LINE, quantity=Decimal(1), unit_price=Decimal("9999999999999999.995")),
        ),
        (
            "line.amount",
            lambda: replace(LINE, quantity=Decimal("9999999999999999.99"), unit_price=Decimal("1E15")),
        ),
    ],
)
def test_each_field_is_checked_and_named_without_its_value(field: str, build: Callable[[], object]) -> None:
    with refused(field) as caught:
        build()
    assert caught.value.field == field
    assert "10185004" not in str(caught.value)


def test_a_cedula_is_taken_wherever_an_rnc_is() -> None:
    assert Issuer(rnc="00113918205", legal_name="JUAN PEREZ", address="CALLE 1").rnc == "00113918205"


def test_quantities_prices_and_amounts_reach_sixteen_integer_digits() -> None:
    most = Decimal("9999999999999999.99")
    assert replace(LINE, quantity=most, unit_price=Decimal(1)).quantity == most
    assert replace(LINE, quantity=Decimal(1), unit_price=Decimal("9999999999999999.9949")).quantity == 1
    assert replace(
        LINE, quantity=Decimal("0.01"), unit_price=Decimal("9999999999999999.9999")
    ).quantity == Decimal("0.01")


@pytest.mark.parametrize("days", [-1, 0])
def test_a_range_is_used_up_to_its_expiry_date(days: int) -> None:
    draft = replace(credit_invoice(), issued_on=SEQUENCE_EXPIRES_ON + timedelta(days=days))
    assert draft.sequence_expires_on == SEQUENCE_EXPIRES_ON


def test_a_range_is_not_used_after_its_expiry_date() -> None:
    with refused("sequence_expires_on") as caught:
        replace(credit_invoice(), issued_on=SEQUENCE_EXPIRES_ON + timedelta(days=1))
    assert caught.value.field == "sequence_expires_on"
