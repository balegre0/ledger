"""Names and codes as people type them, checked once so that the rest of the domain can trust them.

A name is trimmed, its runs of white space are one space, it is in Unicode NFC, it holds no control or format
character, and it is neither empty nor longer than its limit. A code is 1 to ``MAX_CODE`` upper-case ASCII
letters and digits (``001``, ``SUC1``). A fiscal address's line is a name of up to the generic limit, and
its codes are shaped as a catalogue's (``010100``). A refusal says which field and what is wrong, never the
text.
"""

import re
import unicodedata
from typing import Final, NewType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ledger_identity.domain.errors import (
    AddressCodeUnknownError,
    InvalidCodeError,
    InvalidNameError,
    InvalidTimeZoneError,
)
from ledger_kernel.addresses import MAX_ADDRESS_LINE, FiscalAddress, is_subdivision_code

LegalName = NewType("LegalName", str)
"""A company's registered name, as its tax authority knows it."""
Name = NewType("Name", str)
"""The name of an establishment."""
Code = NewType("Code", str)
"""A short code of an establishment, unique in its company."""
TimeZone = NewType("TimeZone", str)
"""The name of an IANA time zone (``America/Santo_Domingo``): where an establishment's days begin and end."""

MAX_LEGAL_NAME: Final = 300
"""Generic (§7.1): a channel enforces its own, shorter limit (an e-CF's ``RazonSocialEmisor`` is 150)."""
MAX_NAME: Final = 100
MAX_CODE: Final = 10
"""The message of ``InvalidCodeError`` spells this limit as a literal: its test pins the two together."""
_CODE: Final = re.compile(rf"[A-Z0-9]{{1,{MAX_CODE}}}", re.ASCII)
_TIME_ZONE: Final = re.compile(r"[A-Za-z0-9_+/-]{1,64}", re.ASCII)
"""What an IANA name is made of: no space, dot or NUL, so that no name can reach beyond the zone database."""
_INVISIBLE: Final = "C"
"""Unicode's "other" categories: controls, formats (zero-width joiners), surrogates, unassigned."""


def _cleaned(field: str, text: str, limit: int) -> str:
    cleaned = unicodedata.normalize("NFC", " ".join(text.split()))
    if not cleaned:
        raise InvalidNameError(field, "is empty")
    if len(cleaned) > limit:
        raise InvalidNameError(field, f"is longer than {limit} characters")
    if any(unicodedata.category(character).startswith(_INVISIBLE) for character in cleaned):
        raise InvalidNameError(field, "holds a control or format character")
    return cleaned


def parse_legal_name(text: str) -> LegalName:
    """A company's legal name, cleaned as the module says."""
    return LegalName(_cleaned("legal_name", text, MAX_LEGAL_NAME))


def parse_name(text: str, *, field: str = "name") -> Name:
    """The name of an establishment; ``field`` is how a refusal names it."""
    return Name(_cleaned(field, text, MAX_NAME))


def _subdivision(field: str, code: str | None) -> str | None:
    if code is not None and not is_subdivision_code(code):
        raise AddressCodeUnknownError(field)
    return code


def parse_address(line: str, *, municipality: str | None, province: str | None) -> FiscalAddress:
    """A fiscal address: its line cleaned as a name is (generic limit), each code checked for its shape.

    Whether the jurisdiction's regime takes it (its line's limit, its catalogue's codes) is
    ``company.require_address_fits``'s to say.
    """
    return FiscalAddress(
        line=_cleaned("address.line", line, MAX_ADDRESS_LINE),
        municipality=_subdivision("address.municipality", municipality),
        province=_subdivision("address.province", province),
    )


def parse_code(text: str) -> Code:
    """A code of upper-case ASCII letters and digits."""
    if _CODE.fullmatch(text) is None:
        raise InvalidCodeError
    return Code(text)


def parse_time_zone(text: str) -> TimeZone:
    """The IANA zone ``text`` names; ``InvalidTimeZoneError`` for anything else, which it never repeats."""
    if _TIME_ZONE.fullmatch(text) is None:
        raise InvalidTimeZoneError
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise InvalidTimeZoneError from error
    return TimeZone(text)
