"""The pins of a caller as identity keeps them (§9.2): its organization's API version.

The organization is pinned to the version current when it was first seen (``Organization.api_version``);
an API client's pin joins it with API clients. The chassis remembers the answer for a while
(``CachedPins``): this reads it once in a short unit of the caller's organization.
"""

from typing import final, override

from ledger_identity.adapters.postgres.repositories import PgOrganizations
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http import ApiVersion, Pinned, Pins
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.data import BoundDatabase


@final
class IdentityPins(Pins):
    """What a principal is pinned to: its organization's version (none before its first company)."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    async def of(self, principal: Principal) -> Pinned:
        async with self._database.unit(OrganizationScope(principal.organization, None)) as unit:
            organization = await PgOrganizations(unit.connection, principal.organization).get(
                principal.organization
            )
        return Pinned(
            client=None,
            organization=None if organization is None else ApiVersion(organization.api_version),
        )
