import json
import operator
from decimal import ROUND_DOWN, Context, Decimal, Inexact, InvalidOperation, localcontext
from typing import Final
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.money import (
    CURRENCIES,
    Currency,
    CurrencyMismatchError,
    InvalidAmountError,
    InvalidCurrencyError,
    Money,
    RoundingMode,
)

DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
EUR: Final = Currency.of("EUR")

# The widest amount there is: 18 integer digits and 24 places, 42 significant digits.
WIDEST: Final = "123456789012345678.123456789012345678901234"
# Bounds that reach 18 integer digits and 24 places while three of them still sum below 10^18.
amounts = st.decimals(min_value=Decimal("-3e17"), max_value=Decimal("3e17"), places=24)
pesos = st.builds(Money, amounts, st.just(DOP))

OPERATIONS: Final = [operator.add, operator.sub, operator.lt, operator.le, operator.gt, operator.ge]
OPERATION_IDS: Final = ["add", "sub", "lt", "le", "gt", "ge"]


def dop(amount: str) -> Money:
    return Money.of(amount, DOP)


def test_amounts_are_read_exactly() -> None:
    assert dop("1250.50").amount == Decimal("1250.50")
    assert Money.of(7, USD) == Money(Decimal(7), USD)
    assert Money.of(Decimal("2.5"), EUR).amount == Decimal("2.5")
    assert dop("-0.01").amount == Decimal("-0.01")
    assert Money.zero(DOP) == dop("0")


def test_minus_zero_reads_as_zero() -> None:
    assert dop("-0") == Money.zero(DOP)
    assert dop("-0.00") == dop("0")
    assert hash(dop("-0")) == hash(Money.zero(DOP))
    assert not dop("-0").amount.is_signed()
    assert str(dop("-0").amount) == "0"


@pytest.mark.parametrize(
    ("zero", "text"),
    [
        (-Money.zero(DOP), "0"),
        (Money.zero(DOP) * -1, "0"),
        (dop("0") * Decimal("-0.50"), "0.00"),
        (dop("-0"), "0"),
        (dop("-0.00"), "0.00"),
        (dop("-0.004").rounded(), "0.00"),
        (-dop("0.00"), "0.00"),
        (Money(Decimal("-0.00"), DOP), "0.00"),
    ],
    ids=[
        "negated-zero",
        "zero-times-minus-one",
        "zero-times-negative-decimal",
        "read-minus-zero",
        "read-minus-zero-with-scale",
        "negative-rounded-to-zero",
        "negated-zero-with-scale",
        "constructed-minus-zero",
    ],
)
def test_zero_is_never_signed_and_keeps_its_scale(zero: Money, text: str) -> None:
    assert str(zero.amount) == text
    assert zero.amount.as_tuple() == Decimal(text).as_tuple()
    assert not zero.amount.is_signed()
    assert zero == Money.zero(DOP)


def test_equality_and_hashing_are_by_value_across_scales() -> None:
    assert dop("1.0") == dop("1.00")
    assert hash(dop("1.0")) == hash(dop("1.00"))
    assert {dop("1.0"): "found"}[dop("1.00")] == "found"
    assert len({dop("1.0"), dop("1.00"), dop("1")}) == 1
    assert dop("1") != Money.of("1", USD)


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
    with pytest.raises(InvalidAmountError, match="is not a plain decimal number"):
        dop(text)


def test_a_huge_text_is_not_echoed_back() -> None:
    with pytest.raises(InvalidAmountError) as caught:
        dop("9" * 100_000)
    assert len(str(caught.value)) < 120


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e18"),
        Decimal("-1e18"),
        Decimal("1e-25"),
        Decimal("1." + "0" * 25),
        10**18,
    ],
    ids=["nan", "infinity", "too-large", "too-negative", "too-precise", "trailing-zeros", "huge-int"],
)
def test_values_out_of_range_are_refused(value: Decimal | int) -> None:
    with pytest.raises(InvalidAmountError):
        Money.of(value, DOP)


def test_a_boolean_is_not_an_amount() -> None:
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        Money.of(amount=True, currency=DOP)


@pytest.mark.parametrize(
    "payload",
    ["12.5", "true", "false", "null", "[1, 2]", '{"amount": 1}'],
    ids=["float", "true", "false", "null", "list", "object"],
)
def test_only_text_integers_and_decimals_are_amounts(payload: str) -> None:
    # `methodcaller` takes untyped arguments, like a real boundary: `json.loads` hands over whatever was sent.
    read = operator.methodcaller("of", json.loads(payload), DOP)
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        read(Money)


@pytest.mark.parametrize(
    "payload",
    ["12.5", "true", "false", "null", '"3"', '"abc"', "[1, 2]", '{"amount": 1}'],
    ids=["float", "true", "false", "null", "text-number", "text", "list", "object"],
)
def test_only_integers_and_decimals_scale_an_amount(payload: str) -> None:
    scale = operator.methodcaller("__mul__", json.loads(payload))
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        scale(dop("3"))


def test_a_wrong_type_is_named_and_not_blamed_on_the_text() -> None:
    read = operator.methodcaller("of", json.loads("12.5"), DOP)
    with pytest.raises(InvalidAmountError) as caught:
        read(Money)
    assert str(caught.value) == "the amount 'float: 12.5' has an unsupported type"
    assert caught.value.code == "invalid_amount"


@pytest.mark.parametrize(
    "payload",
    ["1.5", '"1"', "1", "true", "null", "[1]"],
    ids=["float", "text", "integer", "true", "null", "list"],
)
def test_construction_takes_only_a_decimal_amount(payload: str) -> None:
    build = operator.methodcaller("__call__", json.loads(payload), DOP)
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        build(Money)


def construct_in(payload: str) -> None:
    operator.methodcaller("__call__", Decimal(1), json.loads(payload))(Money)


def read_in(payload: str) -> None:
    operator.methodcaller("of", "1", json.loads(payload))(Money)


def zero_in(payload: str) -> None:
    operator.methodcaller("zero", json.loads(payload))(Money)


@pytest.mark.parametrize(
    "payload",
    ['"USD"', '"GBP"', "null", "1", "true"],
    ids=["currency-as-text", "unknown-code", "null", "number", "true"],
)
@pytest.mark.parametrize("entry_point", [construct_in, read_in, zero_in], ids=["constructor", "of", "zero"])
def test_a_currency_must_be_a_currency_on_every_way_in(
    entry_point: Callable[[str], None], payload: str
) -> None:
    with pytest.raises(InvalidCurrencyError, match="is not an ISO 4217 currency"):
        entry_point(payload)


def test_a_wrong_currency_is_a_short_client_error() -> None:
    build = operator.methodcaller("__call__", Decimal(1), "X" * 10_000)
    with pytest.raises(InvalidCurrencyError) as caught:
        build(Money)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_currency")
    assert len(str(caught.value)) < 120


def test_arithmetic_is_exact() -> None:
    assert dop("0.10") + dop("0.20") == dop("0.30")
    assert dop("10.50") - dop("0.50") == dop("10")
    assert -dop("3") == dop("-3")
    assert dop("10.50") * 3 == dop("31.50")
    assert dop("10.50") * Decimal("0.18") == dop("1.89")


@pytest.mark.parametrize(
    "ambient",
    [Context(prec=28), Context(prec=8, rounding=ROUND_DOWN, traps=[InvalidOperation, Inexact])],
    ids=["default", "tiny-and-strict"],
)
def test_the_widest_amount_survives_every_operation_whatever_the_ambient_context(ambient: Context) -> None:
    zero = Money.zero(DOP)
    with localcontext(ambient):
        widest = dop(WIDEST)
        negated = -widest
        assert negated.amount == Decimal("-" + WIDEST)
        assert -negated == widest
        assert widest + negated == zero
        assert widest - widest == zero
        assert widest + widest == dop("246913578024691356.246913578024691357802468")
        assert widest * 2 == dop("246913578024691356.246913578024691357802468")
        assert widest.rounded() == dop("123456789012345678.12")
        assert negated.rounded() == dop("-123456789012345678.12")


def test_a_result_out_of_range_is_an_error_not_a_rounding() -> None:
    with pytest.raises(InvalidAmountError, match="not below"):
        _ = dop("999999999999999999") + dop("1")
    with pytest.raises(InvalidAmountError, match="not below"):
        _ = dop("999999999999999999") * 2
    with pytest.raises(InvalidAmountError, match="not below"):
        _ = dop("1") * Decimal("1e18")
    with pytest.raises(InvalidAmountError, match="exactly"):
        _ = dop("0." + "1" * 24) * Decimal("0." + "1" * 40)


def test_rounding_that_carries_past_the_range_is_refused() -> None:
    with pytest.raises(InvalidAmountError, match="not below"):
        _ = dop("999999999999999999.995").rounded()


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_currencies_never_mix(operation: Callable[[Money, Money], object]) -> None:
    with pytest.raises(CurrencyMismatchError, match="DOP with USD"):
        operation(dop("1"), Money.of("1", USD))


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_a_value_that_is_not_money_is_a_type_error(operation: Callable[[Money, int], object]) -> None:
    with pytest.raises(TypeError):
        operation(dop("1"), 1)


def test_comparisons() -> None:
    assert dop("1") < dop("2")
    assert dop("2") <= dop("2")
    assert dop("3") > dop("2")
    assert dop("2") >= dop("2")
    assert not dop("2") < dop("2")


def test_negative_amounts_order_by_value() -> None:
    assert dop("-2") < dop("-1") < dop("0") < dop("1")
    assert dop("-2") <= dop("-2")
    assert dop("-1") > dop("-2")
    assert dop("-1") >= dop("-1")
    assert not dop("-1") > dop("0")
    assert not dop("-1") < dop("-2")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0.005", "0.01"), ("-0.005", "-0.01"), ("1.234", "1.23"), ("2.675", "2.68"), ("7", "7.00")],
)
def test_rounding_is_half_away_from_zero_to_the_minor_unit(amount: str, expected: str) -> None:
    assert dop(amount).rounded().amount.as_tuple() == Decimal(expected).as_tuple()


@pytest.mark.parametrize("currency", list(CURRENCIES.values()), ids=list(CURRENCIES))
def test_every_currency_rounds_to_its_minor_unit(currency: Currency) -> None:
    one = Decimal(1).quantize(Decimal(1).scaleb(-currency.minor_units))  # 1, 1.00, 1.000, 1.0000
    assert Money.of("1.000004", currency).rounded().amount.as_tuple() == one.as_tuple()


@given(pesos, pesos, pesos)
def test_addition_is_commutative_and_associative(a: Money, b: Money, c: Money) -> None:
    assert a + b == b + a
    assert (a + b) + c == a + (b + c)
    assert a - b == a + (-b)
    negated = -a
    assert a + negated == Money.zero(DOP)
    assert -negated == a


@given(pesos)
def test_rounding_is_idempotent_and_within_half_a_cent(a: Money) -> None:
    rounded = a.rounded()
    assert rounded.rounded() == rounded
    assert (rounded - a).amount.copy_abs() <= Decimal("0.005")


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (Decimal("1E+5"), "100000"),
        (Decimal("1.2E+3"), "1200"),
        (Decimal("-7E+2"), "-700"),
        (Decimal("0E+3"), "0"),
        (Decimal("-0E+3"), "0"),
        (Decimal("123456789012345678.50"), "123456789012345678.50"),
    ],
    ids=["one-e-five", "fraction-of-a-power", "negative", "zero", "minus-zero", "no-exponent"],
)
def test_a_positive_exponent_is_written_out(amount: Decimal, text: str) -> None:
    """``str`` of a Decimal with a positive exponent prints ``1E+5``: no invoice may show that."""
    money = Money(amount, DOP)
    assert str(money.amount) == text
    assert money.amount.as_tuple() == Decimal(text).as_tuple()
    assert money == Money.of(text, DOP)


def test_arithmetic_never_leaves_a_positive_exponent() -> None:
    assert str((dop("100") * Decimal("1E+3")).amount) == "100000"
    assert str((Money(Decimal("5E+2"), DOP) + dop("0")).amount) == "500"
    assert str((-Money(Decimal("5E+2"), DOP)).amount) == "-500"


@pytest.mark.parametrize("text", ["1.50", "0.001", "-12.340", "7"])
def test_the_scale_of_an_amount_without_a_positive_exponent_is_kept(text: str) -> None:
    assert Money.of(text, DOP).amount.as_tuple() == Decimal(text).as_tuple()


@given(
    coefficient=st.integers(min_value=-(10**12), max_value=10**12),
    exponent=st.integers(min_value=-24, max_value=5),
)
def test_an_amount_keeps_its_value_and_is_written_without_a_positive_exponent(
    coefficient: int, exponent: int
) -> None:
    amount = Decimal(coefficient).scaleb(exponent)
    money = Money(amount, DOP)
    assert money.amount == amount
    stored = money.amount.as_tuple().exponent
    assert isinstance(stored, int)
    assert stored == min(exponent, 0)
    if money.amount.adjusted() >= -6:  # Decimal writes anything smaller in exponent form whatever its scale
        assert "E" not in str(money.amount)


@pytest.mark.parametrize(
    ("code", "numeric", "minor_units"),
    [
        ("DOP", 214, 2),
        ("USD", 840, 2),
        ("EUR", 978, 2),
        ("JPY", 392, 0),
        ("BHD", 48, 3),
        ("CLF", 990, 4),
        ("XCG", 532, 2),
        ("XAD", 396, 2),
    ],
)
def test_the_iso_4217_table_has_each_currency_minor_units(code: str, numeric: int, minor_units: int) -> None:
    assert Currency.of(code) == Currency(code, numeric, minor_units)


def test_codes_and_numbers_are_unique_and_withdrawn_codes_absent() -> None:
    assert len({currency.numeric for currency in CURRENCIES.values()}) == len(CURRENCIES)
    assert all(code == currency.code for code, currency in CURRENCIES.items())
    assert {"ANG", "HRK", "SLL", "ZWL", "CUC", "BGN"}.isdisjoint(CURRENCIES)


@pytest.mark.parametrize("code", ["dop", "XXX", "XAU", "", 214])
def test_anything_else_is_not_a_currency(code: object) -> None:
    with pytest.raises(InvalidCurrencyError):
        Currency.of(code)


def test_a_currency_writes_as_its_code() -> None:
    assert str(DOP) == "DOP"


def test_a_currency_not_from_the_table_cannot_make_money() -> None:
    with pytest.raises(InvalidCurrencyError):
        Money.of("1", Currency("DOP", 214, 3))


def test_rounding_follows_the_currency_minor_unit() -> None:
    assert Money.of("1.5", Currency.of("JPY")).rounded() == Money.of("2", Currency.of("JPY"))
    assert Money.of("1.0005", Currency.of("BHD")).rounded() == Money.of("1.001", Currency.of("BHD"))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (RoundingMode.HALF_UP, "2.13"),
        (RoundingMode.HALF_EVEN, "2.12"),
        (RoundingMode.DOWN, "2.12"),
        (RoundingMode.UP, "2.13"),
    ],
)
def test_quantized_rounds_with_the_mode_asked(mode: RoundingMode, expected: str) -> None:
    assert Money.of("2.125", DOP).quantized(2, mode) == Money.of(expected, DOP)


@pytest.mark.parametrize("places", [-1, 25, True])
def test_quantized_refuses_impossible_places(places: int) -> None:
    with pytest.raises(InvalidValueError):
        Money.of("1", DOP).quantized(places, RoundingMode.HALF_UP)


@pytest.mark.parametrize(
    ("amount", "factor", "places", "mode", "expected"),
    [
        ("10.03", "0.18", 2, RoundingMode.HALF_UP, "1.81"),
        ("10.25", "0.18", 2, RoundingMode.HALF_EVEN, "1.84"),
        ("-10.05", "0.18", 2, RoundingMode.HALF_UP, "-1.81"),
        ("0.000000000000000000000001", "0.18", 24, RoundingMode.HALF_UP, "0"),
        ("0.000000000000000000000009", "0.18", 24, RoundingMode.UP, "0.000000000000000000000002"),
    ],
    ids=["half-up", "half-even", "negative", "below-the-scale", "up-at-the-scale"],
)
def test_times_multiplies_exactly_then_rounds_once(
    amount: str, factor: str, places: int, mode: RoundingMode, expected: str
) -> None:
    assert Money.of(amount, DOP).times(Decimal(factor), places, mode) == Money.of(expected, DOP)


def test_times_takes_a_widest_amount_that_exact_arithmetic_refuses() -> None:
    widest = Money.of(WIDEST, DOP)
    with pytest.raises(InvalidAmountError):
        _ = widest * Decimal("0.18")
    assert widest.times(Decimal("0.18"), 2, RoundingMode.HALF_UP) == Money.of("22222222022222222.06", DOP)


@pytest.mark.parametrize("places", [-1, 25, True])
def test_times_refuses_impossible_places(places: int) -> None:
    with pytest.raises(InvalidValueError):
        Money.of("1", DOP).times(Decimal(1), places, RoundingMode.HALF_UP)


def test_times_refuses_a_float_factor() -> None:
    scale = operator.methodcaller("times", json.loads("0.18"), 2, RoundingMode.HALF_UP)
    with pytest.raises(InvalidAmountError, match="unsupported type"):
        scale(dop("1"))
