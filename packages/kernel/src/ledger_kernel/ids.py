"""Identifiers: UUIDv7 for every entity, a distinct type per entity, and the tenant."""

import re
from dataclasses import dataclass
from typing import ClassVar, Final, NewType, final
from uuid import UUID, uuid7

from ledger_kernel.errors import InvalidInputError, excerpt

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


@final
@dataclass(frozen=True, slots=True)
class Tenant:
    """Whose data a transaction, a request or an event is about."""

    organization: OrganizationId
    company: CompanyId


def new_id() -> UUID:
    """A new identifier: UUIDv7, so ids sort by creation time and index well."""
    return uuid7()


def parse_id(text: str) -> UUID:
    """Read an id in the canonical 8-4-4-4-12 form (either case); every other form is refused."""
    if _CANONICAL.fullmatch(text) is None:
        raise InvalidIdError(text)
    return UUID(text)
