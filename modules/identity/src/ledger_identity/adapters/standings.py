"""How a caller stands, as identity keeps it (§9.2, §9.7): its API client, its organization's API version.

A machine user registered as an API client carries its mode and its version pin; its revocation counts. The
organization is pinned to the version current when it was first seen (``Organization.api_version``). The
chassis remembers the answer for a while (``CachedStandings``): this reads it once, in short units of the
caller's organization. An API client's rows are seen one mode at a time (row-level security), so its mode is
found by looking in each; a machine user is a client in one mode at most.
"""

from typing import final, override

from ledger_identity.adapters.postgres.repositories import PgApiClients, PgOrganizations
from ledger_kernel.tenancy import Mode, OrganizationScope
from ledger_platform.http import ApiVersion, Standing, Standings
lazy from ledger_identity.domain.api_clients import ApiClient
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.data import BoundDatabase


@final
class IdentityStandings(Standings):
    """What a principal stands for: its API client's mode, pin and revocation, and its organization's pin."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    async def of(self, principal: Principal) -> Standing:
        organization = principal.organization
        async with self._database.unit(OrganizationScope(organization, None)) as unit:
            kept = await PgOrganizations(unit.connection, organization).get(organization)
        client = await self._client(principal)
        return Standing(
            mode=None if client is None else client.mode,
            client=None if client is None else ApiVersion(client.api_version),
            organization=None if kept is None else ApiVersion(kept.api_version),
            revoked=client is not None and not client.active,
        )

    async def _client(self, principal: Principal) -> ApiClient | None:
        """The principal's API client, in whichever mode it is bound to.

        The active one; else a revoked one, so that a machine user whose client was revoked stays refused.
        """
        found: list[ApiClient] = []
        for mode in Mode:
            async with self._database.unit(OrganizationScope(principal.organization, mode)) as unit:
                client = await PgApiClients(unit.connection, principal.organization, mode).of_subject(
                    principal.subject
                )
            if client is not None:
                found.append(client)
        return max(found, key=lambda client: (client.active, client.created_at), default=None)
