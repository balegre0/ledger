"""An organization: one Zitadel organization, the customer every company belongs to."""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime

lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.ids import OrganizationId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Organization:
    """What identity keeps of an organization: its id here and at the identity provider, and since when."""

    id: OrganizationId
    provider_id: str
    created_at: datetime

    @classmethod
    def of(cls, principal: Principal, *, now: datetime) -> Organization:
        """The organization ``principal`` acts in, as first seen ``now``."""
        return cls(id=principal.organization, provider_id=principal.provider_organization, created_at=now)
