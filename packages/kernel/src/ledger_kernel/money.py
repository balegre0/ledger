"""Money: exact decimal amounts in a currency. Never a float, never a silent rounding.

Amounts stay below 10^18 with at most 24 decimal places (a 12-place price times a 12-place quantity).
Arithmetic is exact: a result that would need rounding or leave that range is an ``InvalidAmountError``.
Rounding happens only when asked: to the currency's minor unit with ``rounded()``, or as a regime says with
``quantized()``.

Every operation runs in an explicit decimal context, never the ambient one, so no caller can change a result
(or turn a decimal signal into a raw ``decimal`` exception) by configuring the ``decimal`` module.
"""

import re
from dataclasses import dataclass
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, final, override
lazy from collections.abc import Callable, Mapping

from ledger_kernel.errors import InvalidInputError, InvalidValueError, described, excerpt

MAX_INTEGER_DIGITS: Final = 18
MAX_SCALE: Final = 24
_TEXT: Final = re.compile(rf"-?[0-9]{{1,{MAX_INTEGER_DIGITS}}}(?:\.[0-9]{{1,{MAX_SCALE}}})?", re.ASCII)
_PRECISION: Final = 60
_FAILURES: Final[tuple[type[DecimalException], ...]] = (InvalidOperation, Overflow, DivisionByZero)
_EXACT: Final = Context(prec=_PRECISION, rounding=ROUND_HALF_UP, traps=[*_FAILURES, Inexact])
"""Arithmetic: any result that would need rounding is a failure."""
_AMOUNT_TYPES: Final[frozenset[type[object]]] = frozenset({str, int, Decimal})
_FACTOR_TYPES: Final[frozenset[type[object]]] = frozenset({int, Decimal})
_DECIMAL_TYPES: Final[frozenset[type[object]]] = frozenset({Decimal})
_WHOLE: Final = Decimal(1)
"""The exponent of a whole number written out: what an amount with a positive exponent is brought to."""


class RoundingMode(StrEnum):
    """How an amount is rounded when a regime asks for it (``RoundingRule``)."""

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"
    UP = "up"


_DECIMAL_MODES: Final[Mapping[RoundingMode, str]] = MappingProxyType(
    {
        RoundingMode.HALF_UP: ROUND_HALF_UP,
        RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
        RoundingMode.DOWN: ROUND_DOWN,
        RoundingMode.UP: ROUND_UP,
    }
)

# ISO 4217 list A (current currencies and funds that have minor units), as amended to 2026: code, number,
# minor units. Withdrawn: ANG (→ XCG, 2025), HRK (→ EUR, 2023), SLL (→ SLE), ZWL (→ ZWG, 2024), CUC (2021),
# BGN (→ EUR, 2026). Codes without a minor unit (XAU, XAG, XPD, XPT, XDR, XSU, XUA, XBA-XBD, XTS, XXX) are
# not money here. Verify against SIX's list one when it is amended:
# https://www.six-group.com/en/products-services/financial-information/data-standards.html
# A test pins the entries the platform depends on.
_ISO_4217: Final = """
AED 784 2  AFN 971 2  ALL 008 2  AMD 051 2  AOA 973 2  ARS 032 2  AUD 036 2  AWG 533 2  AZN 944 2  BAM 977 2
BBD 052 2  BDT 050 2  BHD 048 3  BIF 108 0  BMD 060 2  BND 096 2  BOB 068 2  BOV 984 2  BRL 986 2  BSD 044 2
BTN 064 2  BWP 072 2  BYN 933 2  BZD 084 2  CAD 124 2  CDF 976 2  CHE 947 2  CHF 756 2  CHW 948 2  CLF 990 4
CLP 152 0  CNY 156 2  COP 170 2  COU 970 2  CRC 188 2  CUP 192 2  CVE 132 2  CZK 203 2  DJF 262 0  DKK 208 2
DOP 214 2  DZD 012 2  EGP 818 2  ERN 232 2  ETB 230 2  EUR 978 2  FJD 242 2  FKP 238 2  GBP 826 2  GEL 981 2
GHS 936 2  GIP 292 2  GMD 270 2  GNF 324 0  GTQ 320 2  GYD 328 2  HKD 344 2  HNL 340 2  HTG 332 2  HUF 348 2
IDR 360 2  ILS 376 2  INR 356 2  IQD 368 3  IRR 364 2  ISK 352 0  JMD 388 2  JOD 400 3  JPY 392 0  KES 404 2
KGS 417 2  KHR 116 2  KMF 174 0  KPW 408 2  KRW 410 0  KWD 414 3  KYD 136 2  KZT 398 2  LAK 418 2  LBP 422 2
LKR 144 2  LRD 430 2  LSL 426 2  LYD 434 3  MAD 504 2  MDL 498 2  MGA 969 2  MKD 807 2  MMK 104 2  MNT 496 2
MOP 446 2  MRU 929 2  MUR 480 2  MVR 462 2  MWK 454 2  MXN 484 2  MXV 979 2  MYR 458 2  MZN 943 2  NAD 516 2
NGN 566 2  NIO 558 2  NOK 578 2  NPR 524 2  NZD 554 2  OMR 512 3  PAB 590 2  PEN 604 2  PGK 598 2  PHP 608 2
PKR 586 2  PLN 985 2  PYG 600 0  QAR 634 2  RON 946 2  RSD 941 2  RUB 643 2  RWF 646 0  SAR 682 2  SBD 090 2
SCR 690 2  SDG 938 2  SEK 752 2  SGD 702 2  SHP 654 2  SLE 925 2  SOS 706 2  SRD 968 2  SSP 728 2  STN 930 2
SVC 222 2  SYP 760 2  SZL 748 2  THB 764 2  TJS 972 2  TMT 934 2  TND 788 3  TOP 776 2  TRY 949 2  TTD 780 2
TWD 901 2  TZS 834 2  UAH 980 2  UGX 800 0  USD 840 2  USN 997 2  UYI 940 0  UYU 858 2  UYW 927 4  UZS 860 2
VED 926 2  VES 928 2  VND 704 0  VUV 548 0  WST 882 2  XAD 396 2  XAF 950 0  XCD 951 2  XCG 532 2  XOF 952 0
XPF 953 0  YER 886 2  ZAR 710 2  ZMW 967 2  ZWG 924 2
"""


@final
@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO 4217 currency: its code, number and the decimal places of its minor unit (data, not an enum)."""

    code: str
    numeric: int
    minor_units: int

    @classmethod
    def of(cls, code: object) -> Currency:
        """The currency ``code`` names, upper case as ISO 4217 writes it, or ``InvalidCurrencyError``."""
        found = CURRENCIES.get(code) if type(code) is str else None
        if found is None:
            raise InvalidCurrencyError(described(code))
        return found

    @override
    def __str__(self) -> str:
        return self.code


def _table(text: str) -> Mapping[str, Currency]:
    words = text.split()
    rows = zip(words[0::3], words[1::3], words[2::3], strict=True)
    return MappingProxyType({code: Currency(code, int(number), int(minor)) for code, number, minor in rows})


CURRENCIES: Final = _table(_ISO_4217)
"""Every currency money can be in, by code."""


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
        super().__init__(f"{value} is not an ISO 4217 currency")


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
    if type(value) is not Currency or CURRENCIES.get(value.code) != value:
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


def _rounded(amount: Decimal, places: int, mode: RoundingMode) -> Decimal:
    """``amount`` rounded to ``places`` (0 to ``MAX_SCALE``) with ``mode``."""
    if type(places) is not int or not 0 <= places <= MAX_SCALE:
        raise InvalidValueError(field="places", requirement=f"an int from 0 to {MAX_SCALE}")
    context = Context(prec=_PRECISION, rounding=_DECIMAL_MODES[mode], traps=list(_FAILURES))
    return _operate(context.quantize, amount, _WHOLE.scaleb(-places))


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
        return self.quantized(self.currency.minor_units, RoundingMode.HALF_UP)

    def quantized(self, places: int, mode: RoundingMode) -> Money:
        """Rounded to ``places`` decimal places with ``mode``: what a regime's ``RoundingRule`` asks for.

        Losing digits is the point here, so ``Inexact`` is not a failure.
        """
        return Money(_rounded(self.amount, places, mode), self.currency)

    def times(self, factor: Decimal | int, places: int, mode: RoundingMode) -> Money:
        """``self * factor`` computed exactly, then rounded once to ``places`` with ``mode``.

        For a product that may hold more places than money does (a 24-place base times a rate): ``*`` refuses
        it, ``times`` rounds it. A product too wide to compute exactly is an ``InvalidAmountError``.
        """
        _require_type(factor, _FACTOR_TYPES)
        product = _operate(_EXACT.multiply, self.amount, Decimal(factor))
        return Money(_rounded(product, places, mode), self.currency)

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
        if other.currency != self.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return other.amount
