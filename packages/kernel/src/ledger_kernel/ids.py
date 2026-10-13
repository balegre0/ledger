"""Identifiers: UUIDv7 for every entity, a distinct type per entity, and their public form (TypeID)."""

import re
from typing import ClassVar, Final, NewType
from uuid import UUID, uuid7

from ledger_kernel.errors import InvalidInputError, LedgerError, excerpt

OrganizationId = NewType("OrganizationId", UUID)
"""A customer organization (one Zitadel organization)."""
CompanyId = NewType("CompanyId", UUID)
"""A company (one RNC) inside an organization: the unit of tenancy."""

_CANONICAL: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.ASCII | re.IGNORECASE
)


class InvalidIdError(InvalidInputError):
    """Text that is not an id in its canonical form."""

    code: ClassVar[str] = "invalid_id"

    def __init__(self, text: str) -> None:
        super().__init__(f"not an id: {excerpt(text)}")


def new_id() -> UUID:
    """A new identifier: UUIDv7, so ids sort by creation time and index well."""
    return uuid7()


def parse_uuid(text: str) -> UUID:
    """Read an id in the canonical 8-4-4-4-12 form (either case); every other form is refused."""
    if _CANONICAL.fullmatch(text) is None:
        raise InvalidIdError(text)
    return UUID(text)


_PREFIX: Final = re.compile(r"[a-z]{2,5}", re.ASCII)
_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"
"""Crockford's base32, lower case: TypeID's alphabet."""
_DIGITS: Final = {character: value for value, character in enumerate(_ALPHABET)}
_SUFFIX_LENGTH: Final = 26
_HIGHEST_FIRST: Final = "7"
"""26 characters hold 130 bits; a UUID has 128, so the first character is at most 7."""


class InvalidIdPrefixError(LedgerError):
    """A public id prefix outside the naming rule (2-5 lower-case letters): a defect of the code naming it."""

    code: ClassVar[str] = "invalid_id_prefix"

    def __init__(self, prefix: str) -> None:
        super().__init__(f"a public id prefix is 2 to 5 lower-case letters, not {excerpt(prefix)}")


class InvalidPublicIdError(InvalidInputError):
    """Text that is not a public id of the expected type."""

    code: ClassVar[str] = "invalid_public_id"

    def __init__(self, prefix: str, text: str) -> None:
        super().__init__(f"not a {prefix}_ id: {excerpt(text)}")


def _require_prefix(prefix: str) -> None:
    if _PREFIX.fullmatch(prefix) is None:
        raise InvalidIdPrefixError(prefix)


def format_id(prefix: str, value: UUID) -> str:
    """The public id (TypeID) of ``value`` with ``prefix``: ``inv_01h455vb4pex5vsknk084sn02q``."""
    _require_prefix(prefix)
    number = value.int
    characters = [""] * _SUFFIX_LENGTH
    for index in range(_SUFFIX_LENGTH - 1, -1, -1):
        characters[index] = _ALPHABET[number & 0b11111]
        number >>= 5
    return f"{prefix}_{''.join(characters)}"


def parse_id(prefix: str, text: str) -> UUID:
    """The UUID inside a public id of type ``prefix``; anything else is an ``InvalidPublicIdError``."""
    _require_prefix(prefix)
    head = f"{prefix}_"
    suffix = text.removeprefix(head)
    if (
        not text.startswith(head)
        or len(suffix) != _SUFFIX_LENGTH
        or suffix[0] > _HIGHEST_FIRST
        or not all(character in _DIGITS for character in suffix)
    ):
        raise InvalidPublicIdError(prefix, text)
    number = 0
    for character in suffix:
        number = number << 5 | _DIGITS[character]
    return UUID(int=number)
