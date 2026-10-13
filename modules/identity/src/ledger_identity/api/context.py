"""What identity's routes are built with: the runtime, the caller, its access, and what ``main`` wires."""

from dataclasses import dataclass
from typing import Annotated, final
lazy from collections.abc import Callable

from fastapi import Depends

from ledger_platform.http.deps import authenticated, company_in_path
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.settings import IdentitySettings
lazy from ledger_platform.auth.access import PermissionSource
lazy from ledger_platform.auth.tokens import TokenVerifier
lazy from ledger_platform.data.database import Database, UnitOfWork
lazy from ledger_platform.http.deps import Caller, InCompany
lazy from ledger_platform.idempotency import Idempotency
lazy from ledger_platform.outbox import Outbox
lazy from ledger_platform.service import Runtime, RuntimeDependency

type BindUnit = Callable[[UnitOfWork, Outbox], IdentityUnit]
"""identity's unit of work over a chassis transaction (``adapters.postgres.unit.bind``)."""
type PermissionsOf = Callable[[Database], PermissionSource]
"""identity's permission source over its database (``adapters.permissions.MembershipPermissions``)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApiContext:
    """The dependencies every router shares, built once with the app.

    The routes see the adapters through ``bind`` and ``permissions`` only: ``main`` hands them in, so the API
    never imports an adapter.
    """

    runtime: RuntimeDependency[IdentitySettings]
    caller: Caller
    in_company: InCompany
    bind: BindUnit
    idempotency: Idempotency

    @classmethod
    def build(
        cls,
        *,
        runtime: RuntimeDependency[IdentitySettings],
        verifier: TokenVerifier,
        bind: BindUnit,
        permissions: PermissionsOf,
        idempotency: Idempotency,
    ) -> ApiContext:
        """The context of identity's routes: the caller from ``verifier``, its access from ``permissions``."""
        caller = authenticated(verifier)

        async def permission_source(
            current: Annotated[Runtime[IdentitySettings], Depends(runtime)],
        ) -> PermissionSource:
            return permissions(current.database)

        return cls(
            runtime=runtime,
            caller=caller,
            in_company=company_in_path(caller, permission_source),
            bind=bind,
            idempotency=idempotency,
        )
