"""What identity's routes are built with: its units, the caller, its access and the idempotency of POSTs."""

from dataclasses import dataclass
from typing import final

from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_platform.http import Idempotency, company_in_path
lazy from ledger_platform.auth import PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, Caller, InCompany


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """What every router of identity shares, built once by the assembly."""

    database: BoundDatabase
    """identity's units, in its schema and role."""
    caller: Caller
    in_company: InCompany
    idempotency: Idempotency

    @classmethod
    def build(cls, *, database: BoundDatabase, authentication: Authentication) -> ApiContext:
        """The context of the routes: the caller from the chassis, its access from identity's memberships."""
        caller = authentication.caller
        permissions = MembershipPermissions(database)

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(
            database=database,
            caller=caller,
            in_company=company_in_path(caller, permission_source),
            idempotency=Idempotency(),
        )
