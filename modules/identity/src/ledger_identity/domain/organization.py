"""An organization: one Zitadel organization, the customer every company belongs to.

Registering a company is an act of the organization as a whole, before the company has any member to grant
roles: it needs the provider's project role ``ORGANIZATION_ADMIN``, which the organization's administrators
hold in Zitadel. Every other act on a company is decided by the actor's role in that company.
"""

from dataclasses import dataclass
from typing import Final, final
lazy from datetime import datetime

from ledger_identity.domain.errors import OrganizationAdminRequiredError
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.ids import OrganizationId

ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role of an organization's administrators: they register its companies."""


def require_organization_admin(principal: Principal) -> None:
    """Refuse a principal the provider did not make ``ORGANIZATION_ADMIN``."""
    if ORGANIZATION_ADMIN not in principal.roles:
        raise OrganizationAdminRequiredError(ORGANIZATION_ADMIN)


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
