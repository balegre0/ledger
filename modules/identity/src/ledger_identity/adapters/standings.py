"""How a caller stands, as identity keeps it (§9.2, §9.7): its organization's API version, its API client.

The organization is pinned to the version current when it was first seen (``Organization.api_version``).
The chassis remembers the answer for a while (``CachedStandings``): this reads it once, in short units of the
caller's organization.
"""

from typing import final, override

from ledger_identity.adapters.postgres.repositories import PgOrganizations
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http import ApiVersion, Standing, Standings
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.data import BoundDatabase


@final
class IdentityStandings(Standings):
    """What a principal stands for: its organization's pin (none before the organization's first company)."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    async def of(self, principal: Principal) -> Standing:
        async with self._database.unit(OrganizationScope(principal.organization, None)) as unit:
            organization = await PgOrganizations(unit.connection, principal.organization).get(
                principal.organization
            )
        return Standing(organization=None if organization is None else ApiVersion(organization.api_version))
