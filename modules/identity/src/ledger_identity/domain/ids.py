"""Identifiers of identity's own entities; organizations and companies use the kernel's."""

from typing import NewType
from uuid import UUID

BranchId = NewType("BranchId", UUID)
EmissionPointId = NewType("EmissionPointId", UUID)
MembershipId = NewType("MembershipId", UUID)
