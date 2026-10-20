"""fiscal's unit of work over a chassis unit: its repository bound to the unit's tenant, events and audit."""

from contextlib import asynccontextmanager
from typing import final, override
lazy from collections.abc import AsyncGenerator
lazy from uuid import UUID

from ledger_fiscal.adapters.postgres.repositories.enrollments import PgEnrollments
from ledger_fiscal.application.ports import Audit, Events, FiscalUnit, FiscalUnits
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
lazy from ledger_fiscal.application.ports import AuditAction
lazy from ledger_fiscal.events import FiscalEvent
lazy from ledger_platform.data import BoundDatabase, UnitOfWork


@final
class UnitEvents(Events):
    """fiscal's events, in the unit's outbox: they commit with the change or not at all (rule 3)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def publish(self, event: FiscalEvent) -> None:
        self._unit.publish(event)


@final
class UnitAudit(Audit):
    """fiscal's audit rows, in the unit's ``audit_events``: the acting call is the actor (Decision 10)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def record(self, action: AuditAction, subject: UUID, *, before: object, after: object) -> None:
        self._unit.audit(action=action, subject=subject, before=before, after=after)


def bind(unit: UnitOfWork) -> FiscalUnit:
    """The fiscal view of ``unit``, which must be scoped to a company in a mode (a defect otherwise)."""
    scope = unit.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="unit.scope", requirement="a company in a mode")
    return FiscalUnit(
        tenant=scope,
        enrollments=PgEnrollments(unit.connection, scope, scope.mode),
        events=UnitEvents(unit),
        audit=UnitAudit(unit),
        now=unit.now,
    )


@final
class PgUnits(FiscalUnits):
    """The units the use cases work in: fiscal's own, in its schema and role, as ``bind`` sees them."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[FiscalUnit]:
        async with self._database.unit(tenant) as unit:
            yield bind(unit)
