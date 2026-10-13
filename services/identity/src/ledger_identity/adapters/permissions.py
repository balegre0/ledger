"""Permissions as identity's own memberships say: the authorization context of identity's own API (§8).

Other services ask identity over gRPC; identity reads its table directly, in a short transaction of the
caller's organization: a company of another organization holds no membership the caller could see.

Identity's own routes get the role's permissions in a deactivated company too, unlike other services
(``queries.permissions_of``): identity refuses every change of such a company itself, with the precise
``company_inactive``, and still lets a role be revoked there, since taking access away is never blocked.
"""

from typing import final, override

from ledger_identity.adapters.postgres.repositories import PgMemberships
from ledger_platform.authorization import PermissionSource
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.database import Database


@final
class MembershipPermissions(PermissionSource):
    """What a principal may do in a company of its organization: the permissions of its active role."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        self._database = database

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        async with self._database.transaction(principal.organization) as unit:
            memberships = PgMemberships(unit.connection, principal.organization)
            membership = await memberships.get(company, principal.subject)
        return frozenset() if membership is None else membership.permissions
