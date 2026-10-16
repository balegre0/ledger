"""Identifiers of identity's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

EstablishmentId = NewType("EstablishmentId", UUID)
MembershipId = NewType("MembershipId", UUID)
