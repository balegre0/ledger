"""Text as clients type it, checked once so that the rest of the domain can trust it.

A free text (a description, a name, an address, a reason) is trimmed, its runs of white space are one space,
it is in Unicode NFC, it holds no control or format character, and it is neither empty nor longer than its
limit. A business key (``external_reference``) is kept exactly as sent, so two keys a client cannot tell apart
are refused instead of cleaned. A refusal says which field and what is wrong, never the text.
"""

import re
import unicodedata
from typing import Final, NewType

from ledger_invoicing.domain.errors import InvalidEmailError, InvalidTextError

Description = NewType("Description", str)
"""What a line sells."""
LegalName = NewType("LegalName", str)
"""A party's name, as its tax authority knows it."""
Address = NewType("Address", str)
Email = NewType("Email", str)
ExternalReference = NewType("ExternalReference", str)
"""A client's business key for a document (§9.5): unique per company, mode and type, forever."""
Reason = NewType("Reason", str)
"""Why a document was voided."""

MAX_DESCRIPTION: Final = 500
MAX_LEGAL_NAME: Final = 300
"""Generic, as identity's (§7.1): a channel enforces its own, shorter limit."""
MAX_ADDRESS: Final = 500
MAX_REASON: Final = 500
MAX_EXTERNAL_REFERENCE: Final = 100
MAX_EMAIL: Final = 254
"""RFC 5321's limit on a path, which bounds an address."""
_EMAIL: Final = re.compile(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+")
_INVISIBLE: Final = "C"
"""Unicode's "other" categories: controls, formats (zero-width joiners), surrogates, unassigned."""


def _invisible(text: str) -> bool:
    return any(unicodedata.category(character).startswith(_INVISIBLE) for character in text)


def _cleaned(field: str, text: str, limit: int) -> str:
    cleaned = unicodedata.normalize("NFC", " ".join(text.split()))
    if not cleaned:
        raise InvalidTextError(field, "is empty")
    if len(cleaned) > limit:
        raise InvalidTextError(field, f"is longer than {limit} characters")
    if _invisible(cleaned):
        raise InvalidTextError(field, "holds a control or format character")
    return cleaned


def parse_description(text: str, *, field: str) -> Description:
    """What a line sells; ``field`` is how a refusal names it (``lines.3.description``)."""
    return Description(_cleaned(field, text, MAX_DESCRIPTION))


def parse_legal_name(text: str, *, field: str) -> LegalName:
    """A party's legal name."""
    return LegalName(_cleaned(field, text, MAX_LEGAL_NAME))


def parse_address(text: str, *, field: str) -> Address:
    """A party's address, on one line."""
    return Address(_cleaned(field, text, MAX_ADDRESS))


def parse_reason(text: str) -> Reason:
    """Why a document is voided."""
    return Reason(_cleaned("reason", text, MAX_REASON))


def parse_email(text: str, *, field: str) -> Email:
    """An email address: one ``@``, a dotted domain, nothing blank or invisible, at most 254 characters."""
    if len(text) > MAX_EMAIL or _EMAIL.fullmatch(text) is None or _invisible(text):
        raise InvalidEmailError(field)
    return Email(text)


def parse_external_reference(text: str) -> ExternalReference:
    """A business key, kept exactly: 1 to 100 characters, no white space around it, nothing invisible."""
    field = "external_reference"
    if not 1 <= len(text) <= MAX_EXTERNAL_REFERENCE:
        raise InvalidTextError(field, f"is not 1 to {MAX_EXTERNAL_REFERENCE} characters")
    if text != text.strip():
        raise InvalidTextError(field, "starts or ends with white space")
    if _invisible(text):
        raise InvalidTextError(field, "holds a control or format character")
    return ExternalReference(text)
