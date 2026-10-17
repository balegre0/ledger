"""Identifiers of identity's own entities; organizations and companies use the kernel's."""

from typing import Final, NewType
from uuid import UUID

ApiClientId = NewType("ApiClientId", UUID)
EstablishmentId = NewType("EstablishmentId", UUID)
MembershipId = NewType("MembershipId", UUID)

API_CLIENT_PREFIX: Final = "cli"
"""The public id prefix of an API client."""
MEMBERSHIP_PREFIX: Final = "mbr"
"""The public id prefix of a membership."""
ESTABLISHMENT_PREFIX: Final = "est"
"""The public id prefix of an establishment."""
