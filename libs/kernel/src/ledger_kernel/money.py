"""Money: exact decimal amounts in a currency. Never a float, never a silent rounding.

Amounts stay below 10^18 with at most 24 decimal places (a 12-place price times a 12-place quantity).
Arithmetic is exact: a result that would need rounding or leave that range is an ``InvalidAmountError``.
Rounding to the currency's minor unit happens only when asked, with ``rounded()``.

Every operation runs in an explicit decimal context, never the ambient one, so no caller can change a result
(or turn a decimal signal into a raw ``decimal`` exception) by configuring the ``decimal`` module.
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

from ledger_kernel.errors import InvalidInputError, described, excerpt

MAX_INTEGER_DIGITS: Final = 18
MAX_SCALE: Final = 24
_TEXT: Final = re.compile(rf"-?[0-9]{{1,{MAX_INTEGER_DIGITS}}}(?:\.[0-9]{{1,{MAX_SCALE}}})?", re.ASCII)
_PRECISION: Final = 60
_FAILURES: Final[tuple[type[DecimalException], ...]] = (InvalidOperation, Overflow, DivisionByZero)
_EXACT: Final = Context(prec=_PRECISION, rounding=ROUND_HALF_UP, traps=[*_FAILURES, Inexact])
"""Arithmetic: any result that would need rounding is a failure."""
_ROUNDING: Final = Context(prec=_PRECISION, rounding=ROUND_HALF_UP, traps=list(_FAILURES))
"""Rounding to the minor unit: losing digits is the point, so ``Inexact`` is not a failure."""
_AMOUNT_TYPES: Final[frozenset[type[object]]] = frozenset({str, int, Decimal})
_FACTOR_TYPES: Final[frozenset[type[object]]] = frozenset({int, Decimal})
_DECIMAL_TYPES: Final[frozenset[type[object]]] = frozenset({Decimal})
_WHOLE: Final = Decimal(1)
"""The exponent of a whole number written out: what an amount with a positive exponent is brought to."""


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
    """The text is malformed."""
    WRONG_TYPE = "has an unsupported type"
    """The value is not text, a number or a Decimal at all, or not of a type this operation takes."""
    NOT_FINITE = "is not a finite number"
    TOO_LARGE = f"is not below 10^{MAX_INTEGER_DIGITS}"
    TOO_PRECISE = f"has more than {MAX_SCALE} decimal places"
    NOT_EXACT = "cannot be computed exactly"


class InvalidAmountError(InvalidInputError):
    """A value that cannot be an amount of money."""

    code: ClassVar[str] = "invalid_amount"

    def __init__(self, problem: AmountProblem, value: str) -> None:
        super().__init__(f"the amount {excerpt(value)} {problem}")


class InvalidCurrencyError(InvalidInputError):
    """A value that is not one of the supported currencies."""

    code: ClassVar[str] = "invalid_currency"

    def __init__(self, value: str) -> None:
        super().__init__(f"the currency {excerpt(value)} is not one of {', '.join(Currency)}")


class CurrencyMismatchError(InvalidInputError):
    """Two amounts in different currencies were combined."""

    code: ClassVar[str] = "currency_mismatch"

    def __init__(self, left: Currency, right: Currency) -> None:
        super().__init__(f"cannot combine {left} with {right}")


def _require_type(value: object, accepted: frozenset[type[object]]) -> None:
    """Refuse a value whose exact runtime type is not accepted (a ``bool`` or a ``float`` among them).

    Annotations are not enforced where input crosses a boundary: ``json.loads`` hands over whatever was sent.
    """
    if type(value) not in accepted:
        raise InvalidAmountError(AmountProblem.WRONG_TYPE, described(value))


def _require_currency(value: object) -> None:
    if type(value) is not Currency:
        raise InvalidCurrencyError(described(value))


def _is_money(value: object) -> bool:
    """Whether ``value`` is money. A predicate on ``object``, so the checkers cannot narrow the operands.

    The operators must answer ``NotImplemented`` to anything else, and Python then raises the ``TypeError``.
    """
    return isinstance(value, Money)


def _require_representable(amount: Decimal) -> None:
    if not amount.is_finite():
        raise InvalidAmountError(AmountProblem.NOT_FINITE, str(amount))
    if amount and amount.adjusted() >= MAX_INTEGER_DIGITS:
        raise InvalidAmountError(AmountProblem.TOO_LARGE, str(amount))
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_SCALE:
        raise InvalidAmountError(AmountProblem.TOO_PRECISE, str(amount))


def _operate(operation: Callable[[Decimal, Decimal], Decimal], left: Decimal, right: Decimal) -> Decimal:
    """Run a decimal operation; any decimal failure becomes an ``InvalidAmountError``."""
    try:
        return operation(left, right)
    except DecimalException as error:
        raise InvalidAmountError(AmountProblem.NOT_EXACT, f"{left} and {right}") from error


@final
@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in one currency.

    Equality and hashing are by value, so ``1.0`` and ``1.00`` are the same amount. The limits (below 10^18,
    at most 24 decimal places) apply to the representation: ``1.`` followed by 25 zeros is refused although
    its value would fit.

    Whichever way a ``Money`` is made (``of``, ``zero``, arithmetic or the constructor) it holds a ``Decimal``
    and a ``Currency`` exactly, and zero is never signed: ``-0.00`` becomes ``0.00``, keeping its scale. A
    positive exponent is written out (``1E+5`` becomes ``100000``), so ``str`` of an amount never prints one;
    the scale of an amount with a fractional part is kept. ``str`` still writes an amount below 10^-6 in
    exponent form (``1E-7``), as ``Decimal`` does: ``format(amount, "f")`` never does.
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        _require_type(self.amount, _DECIMAL_TYPES)
        _require_currency(self.currency)
        _require_representable(self.amount)
        exponent = self.amount.as_tuple().exponent
        if isinstance(exponent, int) and exponent > 0:
            # Exact: a positive exponent means a whole number, which has fewer than 19 digits.
            object.__setattr__(self, "amount", self.amount.quantize(_WHOLE, context=_EXACT))
        if self.amount.is_zero():
            # Frozen, so `object.__setattr__`: a signed zero would print as "-0.00" in a tax document.
            object.__setattr__(self, "amount", self.amount.copy_abs())

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: Currency) -> Money:
        """Read an amount.

        Text must be a plain decimal (``"1250.50"``, ``"-3"``): no exponent, plus sign, spaces or separators.
        """
        _require_type(amount, _AMOUNT_TYPES)
        if isinstance(amount, str) and _TEXT.fullmatch(amount) is None:
            raise InvalidAmountError(AmountProblem.NOT_DECIMAL, amount)
        return cls(Decimal(amount), currency)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """Nothing, in ``currency``."""
        return cls(Decimal(0), currency)

    def rounded(self) -> Money:
        """To the currency's minor unit, half away from zero (0.005 → 0.01, -0.005 → -0.01)."""
        return Money(_operate(_ROUNDING.quantize, self.amount, _MINOR_UNIT[self.currency]), self.currency)

    def __add__(self, other: Money) -> Money:
        if not _is_money(other):
            return NotImplemented
        return Money(_operate(_EXACT.add, self.amount, self._same(other)), self.currency)

    def __sub__(self, other: Money) -> Money:
        if not _is_money(other):
            return NotImplemented
        return Money(_operate(_EXACT.subtract, self.amount, self._same(other)), self.currency)

    def __neg__(self) -> Money:
        return Money(self.amount.copy_negate(), self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        _require_type(factor, _FACTOR_TYPES)
        return Money(_operate(_EXACT.multiply, self.amount, Decimal(factor)), self.currency)

    def __lt__(self, other: Money) -> bool:
        if not _is_money(other):
            return NotImplemented
        return self.amount < self._same(other)

    def __le__(self, other: Money) -> bool:
        if not _is_money(other):
            return NotImplemented
        return self.amount <= self._same(other)

    def __gt__(self, other: Money) -> bool:
        if not _is_money(other):
            return NotImplemented
        return self.amount > self._same(other)

    def __ge__(self, other: Money) -> bool:
        if not _is_money(other):
            return NotImplemented
        return self.amount >= self._same(other)

    def _same(self, other: Money) -> Decimal:
        if other.currency is not self.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return other.amount
