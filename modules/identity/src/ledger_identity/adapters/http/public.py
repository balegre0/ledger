"""Public ids (§9.4): an entity's UUID is shown as a TypeID; a TypeID of another type or form names nothing.

Formatted and read only here, at the HTTP edge. A path that names an id of another type, an internal UUID or
text that is no TypeID of its prefix finds nothing (404), so ids cannot be probed by type, and it is never
repeated back.
"""

from typing import Annotated, Final

from pydantic import StringConstraints

from ledger_identity.domain.errors import ApiClientNotFoundError, EstablishmentNotFoundError
from ledger_identity.domain.ids import (
    API_CLIENT_PREFIX,
    ESTABLISHMENT_PREFIX,
    MEMBERSHIP_PREFIX,
    ApiClientId,
    EstablishmentId,
)
from ledger_kernel.ids import (
    COMPANY_PREFIX,
    ORGANIZATION_PREFIX,
    InvalidPublicIdError,
    format_id,
    parse_id,
)
lazy from ledger_identity.domain.ids import MembershipId
lazy from ledger_kernel.ids import CompanyId, OrganizationId

_SUFFIX: Final = "[0-7][0-9a-hjkmnp-tv-z]{25}"
"""A TypeID's 26 base32 characters (Crockford's alphabet, lower case): 128 bits, so the first is at most 7."""


def _public(prefix: str) -> StringConstraints:
    return StringConstraints(pattern=rf"^{prefix}_{_SUFFIX}$")


type OrganizationRef = Annotated[str, _public(ORGANIZATION_PREFIX)]
"""An organization, by its public id (``org_01h455vb4pex5vsknk084sn02q``)."""
type CompanyRef = Annotated[str, _public(COMPANY_PREFIX)]
"""A company, by its public id (``cmp_…``)."""
type MembershipRef = Annotated[str, _public(MEMBERSHIP_PREFIX)]
"""A membership, by its public id (``mbr_…``)."""
type EstablishmentRef = Annotated[str, _public(ESTABLISHMENT_PREFIX)]
"""An establishment, by its public id (``est_…``)."""
type ApiClientRef = Annotated[str, _public(API_CLIENT_PREFIX)]
"""An API client, by its public id (``cli_…``)."""


def organization_ref(organization: OrganizationId) -> str:
    """The public id of ``organization``."""
    return format_id(ORGANIZATION_PREFIX, organization)


def company_ref(company: CompanyId) -> str:
    """The public id of ``company``."""
    return format_id(COMPANY_PREFIX, company)


def membership_ref(membership: MembershipId) -> str:
    """The public id of ``membership``."""
    return format_id(MEMBERSHIP_PREFIX, membership)


def api_client_ref(client: ApiClientId) -> str:
    """The public id of ``client``."""
    return format_id(API_CLIENT_PREFIX, client)


def parse_api_client_ref(text: str) -> ApiClientId:
    """The API client ``text`` names; ``ApiClientNotFoundError`` for anything but its public id."""
    try:
        return ApiClientId(parse_id(API_CLIENT_PREFIX, text))
    except InvalidPublicIdError:
        raise ApiClientNotFoundError from None


def establishment_ref(establishment: EstablishmentId) -> str:
    """The public id of ``establishment``."""
    return format_id(ESTABLISHMENT_PREFIX, establishment)


def parse_establishment_ref(text: str) -> EstablishmentId:
    """The establishment ``text`` names; ``EstablishmentNotFoundError`` for anything but its public id."""
    try:
        return EstablishmentId(parse_id(ESTABLISHMENT_PREFIX, text))
    except InvalidPublicIdError:
        raise EstablishmentNotFoundError from None
