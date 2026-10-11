"""Dominican Republic taxpayer identifiers: RNC (9 digits) and cédula (11 digits).

DGII accepts a cédula wherever an RNC is asked for. Check digits are reported, not enforced: DGII has
issued real numbers whose check digit does not match, so whether a number exists is for DGII's registry
to say, consulted by the services that need it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Literal, final, override
lazy from collections.abc import Callable, Mapping

from ledger_kernel.errors import InvalidInputError, described, excerpt

_RNC_WEIGHTS: Final = (7, 9, 8, 6, 5, 4, 3, 2)
_MAX_TEXT: Final = 20
"""Longest text read: 11 digits and their separators with room to spare; a longer one is refused unread."""
_LUHN_MAX_DIGIT: Final = 9


class TaxIdKind(StrEnum):
    """Which identifier."""

    RNC = "rnc"
    CEDULA = "cedula"


type TaxIdField = Literal["text", "digits", "kind"]
"""The arguments that are refused for their type: what ``parse`` reads, and what the constructor holds."""


class InvalidTaxIdError(InvalidInputError):
    """Text that is neither an RNC nor a cédula, or an argument that is not of the type it needs.

    For the second case ``text`` describes what arrived (see ``described``) and ``field`` says which argument
    it was, so a wrong ``kind`` is not read as wrong digits.
    """

    code: ClassVar[str] = "invalid_tax_id"

    def __init__(self, text: str, *, field: TaxIdField | None = None) -> None:
        shown = excerpt(text) if field is None else f"{field}={excerpt(text)}"
        super().__init__(f"not an RNC or cédula: {shown}")


def _require_type(value: object, expected: type[object], field: TaxIdField) -> None:
    """Refuse a value whose exact runtime type is not ``expected``.

    Annotations are not enforced where input crosses a boundary: ``json.loads`` hands over whatever was sent.
    Exact, because the text ``"rnc"`` equals ``TaxIdKind.RNC`` and hashes like it.
    """
    if type(value) is not expected:
        raise InvalidTaxIdError(described(value), field=field)


def _is_ascii_digits(text: str) -> bool:
    """Whether ``text`` is made of ``0``-``9`` only: ``str.isdigit`` alone also takes Arabic-Indic digits."""
    return text.isascii() and text.isdigit()


def _rnc_check_digit(first_eight: str) -> int:
    remainder = sum(weight * int(digit) for weight, digit in zip(_RNC_WEIGHTS, first_eight, strict=True)) % 11
    return (10 - remainder) % 9 + 1


def _rnc_valid(digits: str) -> bool:
    return _rnc_check_digit(digits[:8]) == int(digits[8])


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


_LENGTH: Final[Mapping[TaxIdKind, int]] = {TaxIdKind.RNC: 9, TaxIdKind.CEDULA: 11}
_KIND_BY_LENGTH: Final[Mapping[int, TaxIdKind]] = {length: kind for kind, length in _LENGTH.items()}
_CHECK_DIGIT_MATCHES: Final[Mapping[TaxIdKind, Callable[[str], bool]]] = {
    TaxIdKind.RNC: _rnc_valid,
    TaxIdKind.CEDULA: _luhn_valid,
}


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
        _require_type(self.digits, str, "digits")
        _require_type(self.kind, TaxIdKind, "kind")
        if len(self.digits) != _LENGTH[self.kind] or not _is_ascii_digits(self.digits):
            raise InvalidTaxIdError(self.digits)

    @classmethod
    def parse(cls, text: str) -> TaxId:
        """Read an RNC or a cédula; hyphens and spaces are ignored and the digit count picks the kind."""
        _require_type(text, str, "text")
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
        return _CHECK_DIGIT_MATCHES[self.kind](self.digits)

    @override
    def __str__(self) -> str:
        return self.digits
