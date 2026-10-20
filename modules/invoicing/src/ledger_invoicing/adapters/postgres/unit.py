"""invoicing's unit of work over a chassis unit: the tenant's documents, its events and its audit."""

from contextlib import asynccontextmanager
from typing import ClassVar, final, override
lazy from collections.abc import AsyncGenerator, Mapping
lazy from uuid import UUID

from ledger_invoicing.adapters.postgres.repositories import PgInvoices
from ledger_invoicing.application.ports import Audit, Events, InvoicingUnit, InvoicingUnits
from ledger_kernel.errors import LedgerError
from ledger_kernel.tenancy import Tenant
lazy from ledger_invoicing.application.ports import AuditAction
lazy from ledger_invoicing.events import InvoicingEvent
lazy from ledger_platform.data import BoundDatabase, UnitOfWork


class UnscopedUnitError(LedgerError):
    """invoicing's state was to be read in a unit of no company, or of no mode: a defect of the caller."""

    code: ClassVar[str] = "invoicing_unit_unscoped"

    def __init__(self) -> None:
        super().__init__("invoicing's state is read and changed in a unit of a company in a mode")


@final
class UnitEvents(Events):
    """invoicing's events, in the unit's outbox: they commit with the change or not at all (rule 3)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def publish(self, event: InvoicingEvent) -> None:
        self._unit.publish(event)


@final
class UnitAudit(Audit):
    """invoicing's audit rows, in the unit's ``audit_events``: the acting call is the actor."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        self._unit.audit(action=action, subject=subject, before=before, after=after, detail=detail)


def bind(unit: UnitOfWork) -> InvoicingUnit:
    """The invoicing view of ``unit``, which must be scoped to a company in a mode.

    The repository is bound to that tenant, which it names in its statements as row-level security enforces
    it. The unit's time is the database's, read once when it began.
    """
    scope = unit.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise UnscopedUnitError
    return InvoicingUnit(
        invoices=PgInvoices(unit.connection, scope, scope.mode),
        events=UnitEvents(unit),
        audit=UnitAudit(unit),
        now=unit.now,
    )


@final
class PgUnits(InvoicingUnits):
    """The units the use cases work in: invoicing's own, in its schema and role, as ``bind`` sees them."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[InvoicingUnit]:
        async with self._database.unit(tenant) as unit:
            yield bind(unit)
