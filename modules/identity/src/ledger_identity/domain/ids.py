"""Identifiers of identity's own entities; organizations and companies use the kernel's."""

from typing import Final, NewType
from uuid import UUID

EstablishmentId = NewType("EstablishmentId", UUID)
MembershipId = NewType("MembershipId", UUID)

MEMBERSHIP_PREFIX: Final = "mbr"
"""The public id prefix of a membership."""
ESTABLISHMENT_PREFIX: Final = "est"
"""The public id prefix of an establishment."""
