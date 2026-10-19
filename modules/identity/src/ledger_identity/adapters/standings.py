"""How a caller stands, as identity keeps it (§9.2, §9.7): its API client, its organization's API version.

A machine user registered as an API client carries its mode and its version pin; its revocation counts. A
person is never one: its API clients are not looked for. The organization is pinned when identity first keeps
it (its first company, or its first API client), to the version current then (``Organization.api_version``);
until then nothing is known of it, and its requests speak what they name, else the current version. The
chassis remembers the answer per replica (``CachedStandings``: 30 s, an answer that knows nothing 5 s, one
question at a time per caller): this reads it in one transaction of the caller's organization, where API
clients are seen whatever their mode (revision 0003); a machine user is an active client in one mode at most.
"""

from time import monotonic
from typing import Final, final, override

from ledger_identity.adapters.postgres.repositories import PgOrganizations, client_of_subject
from ledger_kernel.access import PrincipalKind
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.http import ApiVersion, Standing, StandingsPort
from ledger_platform.runtime.standings import CachedStandings
lazy from ledger_kernel.access import Principal
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_platform.data import BoundDatabase

STANDING_TTL: Final = 30.0
"""Seconds a replica remembers how a caller stands: a change (an API client revoked) shows within this."""
STANDING_CACHE: Final = 10_000
"""How many callers' standings each replica remembers at most."""


@final
class IdentityStandings(StandingsPort):
    """What a principal stands for: its API client's mode, pin and revocation, and its organization's pin."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    async def of(self, principal: Principal) -> Standing:
        organization = principal.organization
        async with self._database.unit(OrganizationScope(organization, None)) as unit:
            kept = await PgOrganizations(unit.connection, organization).get(organization)
            client = (
                None
                if principal.kind is PrincipalKind.HUMAN
                else await client_of_subject(unit.connection, organization, principal.subject)
            )
        return Standing(
            mode=None if client is None else client.mode,
            client=None if client is None else ApiVersion(client.api_version),
            organization=None if kept is None else ApiVersion(kept.api_version),
            revoked=client is not None and not client.active,
        )


def cached_standings(database: BoundDatabase, clock: MonotonicClock = monotonic) -> CachedStandings:
    """What identity provides as the platform's ``StandingsPort``: its standings, remembered per replica."""
    return CachedStandings(IdentityStandings(database), ttl=STANDING_TTL, size=STANDING_CACHE, clock=clock)
