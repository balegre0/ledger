"""Names and codes as people type them, checked once so that the rest of the domain can trust them.

A name is trimmed, its runs of white space are one space, it is in Unicode NFC, it holds no control or format
character, and it is neither empty nor longer than its limit. A code is 1 to ``MAX_CODE`` upper-case ASCII
letters and digits (``001``, ``SUC1``). A refusal says which field and what is wrong, never the text.
"""

import re
import unicodedata
from typing import Final, NewType

from ledger_identity.domain.errors import InvalidCodeError, InvalidNameError

LegalName = NewType("LegalName", str)
"""A company's registered name, as DGII knows it."""
Name = NewType("Name", str)
"""The name of a branch or an emission point."""
Code = NewType("Code", str)
"""A short code of a branch or an emission point, unique where it lives."""

MAX_LEGAL_NAME: Final = 150
"""As long as an e-CF's ``RazonSocialEmisor`` may be."""
MAX_NAME: Final = 100
MAX_CODE: Final = 10
_CODE: Final = re.compile(rf"[A-Z0-9]{{1,{MAX_CODE}}}", re.ASCII)
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
    """The name of a branch or an emission point; ``field`` is how a refusal names it."""
    return Name(_cleaned(field, text, MAX_NAME))


def parse_code(text: str) -> Code:
    """A code of upper-case ASCII letters and digits."""
    if _CODE.fullmatch(text) is None:
        raise InvalidCodeError
    return Code(text)
