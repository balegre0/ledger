"""Dominican taxpayer identifiers: ``do.rnc`` (9 digits) and ``do.cedula`` (11 digits), check digits reported.

DGII accepts a cédula wherever an RNC is asked for, and has issued numbers whose check digit does not match
(python-stdnum keeps hundreds of such cédulas), so the check digit is reported, never enforced
(``check_digit_enforced = False``, ADR 0006): whether a number exists is for DGII's registry to say. Hyphens
and spaces are ignored; anything else that is not an ASCII digit is a format error. A check never repeats the
text it read.
"""

from typing import Final

from ledger_country_do.regime.definitions import TaxIdCheck, TaxIdSchemeDef
from ledger_country_do.regime.sources import STDNUM_CEDULA, STDNUM_RNC

MAX_TEXT: Final = 20
"""The longest text read: eleven digits and their separators, with room to spare."""
RNC_DIGITS: Final = 9
CEDULA_DIGITS: Final = 11
_RNC_WEIGHTS: Final = (7, 9, 8, 6, 5, 4, 3, 2)
_SEPARATORS: Final = str.maketrans("", "", "- ")
_LUHN_FOLD: Final = 9


def normalize(text: str) -> str | None:
    """``text``'s digits without hyphens and spaces; ``None`` unless that leaves ASCII digits only."""
    if len(text) > MAX_TEXT:
        return None
    digits = text.translate(_SEPARATORS)
    return digits if digits.isascii() and digits.isdigit() else None


def _rnc_matches(digits: str) -> bool:
    remainder = sum(weight * int(digit) for weight, digit in zip(_RNC_WEIGHTS, digits[:8], strict=True)) % 11
    return (10 - remainder) % 9 + 1 == int(digits[8])


def _luhn_matches(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        digit = int(character) * (2 if position % 2 else 1)
        total += digit - _LUHN_FOLD if digit > _LUHN_FOLD else digit
    return total % 10 == 0


def check_rnc(text: str) -> TaxIdCheck:
    """An RNC's check: nine digits, the last the one DGII's weights predict."""
    digits = normalize(text)
    if digits is None or len(digits) != RNC_DIGITS:
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if _rnc_matches(digits) else TaxIdCheck.CHECKSUM_MISMATCH


def check_cedula(text: str) -> TaxIdCheck:
    """A cédula's check: eleven digits, the last the one Luhn's algorithm predicts."""
    digits = normalize(text)
    if digits is None or len(digits) != CEDULA_DIGITS:
        return TaxIdCheck.INVALID_FORMAT
    return TaxIdCheck.VALID if _luhn_matches(digits) else TaxIdCheck.CHECKSUM_MISMATCH


RNC: Final = TaxIdSchemeDef(
    key="do.rnc",
    label="RNC",
    digits=RNC_DIGITS,
    check_digit_enforced=False,
    check=check_rnc,
    source=STDNUM_RNC,
)
CEDULA: Final = TaxIdSchemeDef(
    key="do.cedula",
    label="Cédula",
    digits=CEDULA_DIGITS,
    check_digit_enforced=False,
    check=check_cedula,
    source=STDNUM_CEDULA,
)
SCHEMES: Final = (RNC, CEDULA)


def scheme_of(text: str) -> TaxIdSchemeDef | None:
    """The scheme ``text``'s digit count names (a cédula is taken wherever an RNC is); ``None`` if neither."""
    digits = normalize(text)
    if digits is None:
        return None
    return next((scheme for scheme in SCHEMES if scheme.digits == len(digits)), None)
