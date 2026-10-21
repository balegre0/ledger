"""What fiscal's routes are built with: its use cases, the caller, its access and the keys of POSTs."""

from dataclasses import dataclass
from typing import final

from ledger_platform.http import Idempotency, company_in_header, ledger_mode_of
lazy from ledger_fiscal.application.enrolling import Enrolling
lazy from ledger_platform.auth import PermissionSource
lazy from ledger_platform.data import BoundDatabase
lazy from ledger_platform.http import Authentication, InCompany
lazy from ledger_platform.http.mode import ModeDependency


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """What every route of fiscal shares, built once by the assembly."""

    database: BoundDatabase
    """fiscal's units, in its schema and role: where a POST claims its key with its change."""
    enrolling: Enrolling
    mode: ModeDependency
    """The request's mode, settled once its caller is proven (``ledger_mode_of``)."""
    in_company: InCompany
    """The company of ``Ledger-Company`` and what the caller may do there, as identity says."""
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        database: BoundDatabase,
        authentication: Authentication,
        permissions: PermissionSource,
        enrolling: Enrolling,
    ) -> ApiContext:
        """The context of the routes: the chassis's caller, its access in the company as identity says."""
        caller = authentication.caller

        async def permission_source() -> PermissionSource:
            return permissions

        return cls(
            database=database,
            enrolling=enrolling,
            mode=ledger_mode_of(caller),
            in_company=company_in_header(caller, permission_source),
            idempotency=Idempotency(),
        )
