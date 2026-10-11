from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.money import Currency, CurrencyMismatchError, InvalidAmountError, Money

amounts = st.decimals(min_value=Decimal("-1e15"), max_value=Decimal("1e15"), places=6)
pesos = st.builds(Money, amounts, st.just(Currency.DOP))


def dop(amount: str) -> Money:
    return Money.of(amount, Currency.DOP)


def test_amounts_are_read_exactly() -> None:
    assert dop("1250.50").amount == Decimal("1250.50")
    assert Money.of(7, Currency.USD) == Money(Decimal(7), Currency.USD)
    assert Money.of(Decimal("2.5"), Currency.EUR).amount == Decimal("2.5")
    assert dop("-0.01").amount == Decimal("-0.01")
    assert Money.zero(Currency.DOP) == dop("0")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "abc",
        "1e3",
        "NaN",
        "Infinity",
        " 1",
        "1 ",
        "1_000",
        "١٢",
        "1.",
        ".5",
        "+1",
        "1" * 19,
        "0." + "1" * 25,
    ],
)
def test_text_that_is_not_a_plain_decimal_is_refused(text: str) -> None:
    with pytest.raises(InvalidAmountError, match="amount"):
        dop(text)


def test_a_huge_text_is_not_echoed_back() -> None:
    with pytest.raises(InvalidAmountError) as caught:
        dop("9" * 100_000)
    assert len(str(caught.value)) < 120


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("1e18"), Decimal("-1e18"), Decimal("1e-25"), 10**18],
    ids=["nan", "infinity", "too-large", "too-small", "too-precise", "huge-int"],
)
def test_values_out_of_range_are_refused(value: Decimal | int) -> None:
    with pytest.raises(InvalidAmountError):
        Money.of(value, Currency.DOP)


def test_a_boolean_is_not_an_amount() -> None:
    with pytest.raises(InvalidAmountError):
        Money.of(amount=True, currency=Currency.DOP)


def test_arithmetic_is_exact() -> None:
    assert dop("0.10") + dop("0.20") == dop("0.30")
    assert dop("10.50") - dop("0.50") == dop("10")
    assert -dop("3") == dop("-3")
    assert dop("10.50") * 3 == dop("31.50")
    assert dop("10.50") * Decimal("0.18") == dop("1.89")


def test_a_result_out_of_range_is_an_error_not_a_rounding() -> None:
    with pytest.raises(InvalidAmountError):
        _ = dop("999999999999999999") + dop("1")
    with pytest.raises(InvalidAmountError):
        _ = dop("0." + "1" * 24) * Decimal("0." + "1" * 40)


def test_currencies_never_mix() -> None:
    usd = Money.of("1", Currency.USD)
    for operation in (
        lambda: dop("1") + usd,
        lambda: dop("1") - usd,
        lambda: dop("1") < usd,
        lambda: dop("1") <= usd,
        lambda: dop("1") > usd,
        lambda: dop("1") >= usd,
    ):
        with pytest.raises(CurrencyMismatchError, match="DOP with USD"):
            operation()


def test_comparisons() -> None:
    assert dop("1") < dop("2")
    assert dop("2") <= dop("2")
    assert dop("3") > dop("2")
    assert dop("2") >= dop("2")
    assert not dop("2") < dop("2")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0.005", "0.01"), ("-0.005", "-0.01"), ("1.234", "1.23"), ("2.675", "2.68"), ("7", "7.00")],
)
def test_rounding_is_half_away_from_zero_to_the_minor_unit(amount: str, expected: str) -> None:
    assert dop(amount).rounded().amount.as_tuple() == Decimal(expected).as_tuple()


@given(pesos, pesos, pesos)
def test_addition_is_commutative_and_associative(a: Money, b: Money, c: Money) -> None:
    assert a + b == b + a
    assert (a + b) + c == a + (b + c)
    assert a - b == a + (-b)
    assert a + (-a) == Money.zero(Currency.DOP)


@given(pesos)
def test_rounding_is_idempotent_and_within_half_a_cent(a: Money) -> None:
    rounded = a.rounded()
    assert rounded.rounded() == rounded
    assert abs(rounded.amount - a.amount) <= Decimal("0.005")
