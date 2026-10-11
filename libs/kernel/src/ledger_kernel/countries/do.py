"""Dominican Republic taxpayer identifiers: RNC (9 digits) and cédula (11 digits).

DGII accepts a cédula wherever an RNC is asked for. Check digits are reported, not enforced: DGII has
issued real numbers whose check digit does not match, so whether a number exists is for DGII's registry
to say, consulted by the services that need it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final, override
lazy from collections.abc import Mapping

from ledger_kernel.errors import InvalidInputError, excerpt

_RNC_WEIGHTS: Final = (7, 9, 8, 6, 5, 4, 3, 2)
_MAX_TEXT: Final = 20
"""Longest text read: 11 digits and their separators with room to spare; a longer one is refused unread."""
_LUHN_MAX_DIGIT: Final = 9


class TaxIdKind(StrEnum):
    """Which identifier."""

    RNC = "rnc"
    CEDULA = "cedula"


_LENGTH: Final[Mapping[TaxIdKind, int]] = {TaxIdKind.RNC: 9, TaxIdKind.CEDULA: 11}
_KIND_BY_LENGTH: Final[Mapping[int, TaxIdKind]] = {length: kind for kind, length in _LENGTH.items()}


class InvalidTaxIdError(InvalidInputError):
    """Text that is neither an RNC nor a cédula, or a value that is not text (or not a kind) at all."""

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, text: str) -> None:
        super().__init__(f"not an RNC or cédula: {excerpt(text)}")


def _described(value: object) -> str:
    """The value led by its type, so ``int: 101850043`` is never mistaken for the text ``101850043``."""
    return f"{type(value).__name__}: {value}"


def _require_text(value: object) -> None:
    """Refuse a value that is not exactly ``str``.

    Annotations are not enforced where input crosses a boundary: ``json.loads`` hands over whatever was sent.
    """
    if type(value) is not str:
        raise InvalidTaxIdError(_described(value))


def _require_kind(value: object) -> None:
    """Refuse a value that is not exactly a ``TaxIdKind``: the text ``"rnc"`` equals ``TaxIdKind.RNC``."""
    if type(value) is not TaxIdKind:
        raise InvalidTaxIdError(_described(value))


def _is_ascii_digits(text: str) -> bool:
    """Whether ``text`` is made of ``0``-``9`` only: ``str.isdigit`` alone also takes Arabic-Indic digits."""
    return text.isascii() and text.isdigit()


def _rnc_check_digit(first_eight: str) -> int:
    remainder = sum(weight * int(digit) for weight, digit in zip(_RNC_WEIGHTS, first_eight, strict=True)) % 11
    return (10 - remainder) % 9 + 1


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character)
        if position % 2 == 1:
            digit *= 2
            if digit > _LUHN_MAX_DIGIT:
                digit -= _LUHN_MAX_DIGIT
        total += digit
    return total % 10 == 0


@final
@dataclass(frozen=True, slots=True)
class TaxId:
    """An RNC or a cédula, digits only.

    Whichever way a ``TaxId`` is made (``parse`` or the constructor) it holds a ``str`` of ASCII digits and
    a ``TaxIdKind`` exactly, and the number of digits is the one the kind has.
    """

    digits: str
    kind: TaxIdKind

    def __post_init__(self) -> None:
        _require_text(self.digits)
        _require_kind(self.kind)
        if len(self.digits) != _LENGTH[self.kind] or not _is_ascii_digits(self.digits):
            raise InvalidTaxIdError(self.digits)

    @classmethod
    def parse(cls, text: str) -> TaxId:
        """Read an RNC or a cédula; hyphens and spaces are ignored and the digit count picks the kind."""
        _require_text(text)
        if len(text) > _MAX_TEXT:
            raise InvalidTaxIdError(text)
        digits = text.replace("-", "").replace(" ", "")
        kind = _KIND_BY_LENGTH.get(len(digits))
        if kind is None or not _is_ascii_digits(digits):
            raise InvalidTaxIdError(text)
        return cls(digits, kind)

    @property
    def check_digit_matches(self) -> bool:
        """Whether the last digit is the one the number's algorithm predicts."""
        if self.kind is TaxIdKind.RNC:
            return _rnc_check_digit(self.digits[:8]) == int(self.digits[8])
        return _luhn_valid(self.digits)

    @override
    def __str__(self) -> str:
        return self.digits
