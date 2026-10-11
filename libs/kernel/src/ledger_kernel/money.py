"""Money: exact decimal amounts in a currency. Never a float, never a silent rounding.

Amounts stay below 10^18 with at most 24 decimal places (a 12-place price times a 12-place quantity).
Arithmetic is exact: a result that would need rounding or leave that range is an ``InvalidAmountError``.
Rounding to the currency's minor unit happens only when asked, with ``rounded()``.
"""

import re
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping

from ledger_kernel.errors import InvalidInputError, excerpt

MAX_INTEGER_DIGITS: Final = 18
MAX_SCALE: Final = 24
_TEXT: Final = re.compile(rf"-?[0-9]{{1,{MAX_INTEGER_DIGITS}}}(?:\.[0-9]{{1,{MAX_SCALE}}})?", re.ASCII)
_EXACT: Final = Context(
    prec=60, rounding=ROUND_HALF_UP, traps=[InvalidOperation, Inexact, Overflow, DivisionByZero]
)


class Currency(StrEnum):
    """ISO 4217 currencies the platform handles."""

    DOP = "DOP"
    USD = "USD"
    EUR = "EUR"


_MINOR_UNIT: Final[Mapping[Currency, Decimal]] = {
    Currency.DOP: Decimal("0.01"),
    Currency.USD: Decimal("0.01"),
    Currency.EUR: Decimal("0.01"),
}


class AmountProblem(StrEnum):
    """Why a value is not an acceptable amount."""

    NOT_DECIMAL = "is not a plain decimal number"
    NOT_FINITE = "is not a finite number"
    TOO_LARGE = f"is not below 10^{MAX_INTEGER_DIGITS}"
    TOO_PRECISE = f"has more than {MAX_SCALE} decimal places"
    NOT_EXACT = "cannot be computed exactly"


class InvalidAmountError(InvalidInputError):
    """A value that cannot be an amount of money."""

    code: ClassVar[str] = "invalid_amount"

    def __init__(self, problem: AmountProblem, value: str) -> None:
        super().__init__(f"the amount {excerpt(value)} {problem}")


class CurrencyMismatchError(InvalidInputError):
    """Two amounts in different currencies were combined."""

    code: ClassVar[str] = "currency_mismatch"

    def __init__(self, left: Currency, right: Currency) -> None:
        super().__init__(f"cannot combine {left} with {right}")


def _checked(amount: Decimal) -> Decimal:
    if not amount.is_finite():
        raise InvalidAmountError(AmountProblem.NOT_FINITE, str(amount))
    if amount and amount.adjusted() >= MAX_INTEGER_DIGITS:
        raise InvalidAmountError(AmountProblem.TOO_LARGE, str(amount))
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_SCALE:
        raise InvalidAmountError(AmountProblem.TOO_PRECISE, str(amount))
    return amount


def _exact(operation: Callable[[Decimal, Decimal], Decimal], left: Decimal, right: Decimal) -> Decimal:
    try:
        return operation(left, right)
    except DecimalException as error:
        raise InvalidAmountError(AmountProblem.NOT_EXACT, f"{left} and {right}") from error


@final
@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in one currency."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        _checked(self.amount)

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: Currency) -> Money:
        """Read an amount.

        Text must be a plain decimal (``"1250.50"``, ``"-3"``): no exponent, plus sign, spaces or separators.
        """
        if isinstance(amount, bool):
            raise InvalidAmountError(AmountProblem.NOT_DECIMAL, str(amount))
        if isinstance(amount, str):
            if _TEXT.fullmatch(amount) is None:
                raise InvalidAmountError(AmountProblem.NOT_DECIMAL, amount)
            return cls(Decimal(amount), currency)
        return cls(Decimal(amount), currency)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """Nothing, in ``currency``."""
        return cls(Decimal(0), currency)

    def rounded(self) -> Money:
        """To the currency's minor unit, half away from zero (0.005 → 0.01, -0.005 → -0.01)."""
        return Money(self.amount.quantize(_MINOR_UNIT[self.currency], rounding=ROUND_HALF_UP), self.currency)

    def __add__(self, other: Money) -> Money:
        return Money(_exact(_EXACT.add, self.amount, self._same(other)), self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(_exact(_EXACT.subtract, self.amount, self._same(other)), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(_exact(_EXACT.multiply, self.amount, Decimal(factor)), self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.amount < self._same(other)

    def __le__(self, other: Money) -> bool:
        return self.amount <= self._same(other)

    def __gt__(self, other: Money) -> bool:
        return self.amount > self._same(other)

    def __ge__(self, other: Money) -> bool:
        return self.amount >= self._same(other)

    def _same(self, other: Money) -> Decimal:
        if other.currency is not self.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return other.amount
