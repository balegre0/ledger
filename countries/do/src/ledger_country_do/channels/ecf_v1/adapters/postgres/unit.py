"""The channel's unit of work over a chassis unit: repositories bound to its tenant, outbox, audit, jobs."""

from contextlib import asynccontextmanager
from typing import final, override
lazy from collections.abc import AsyncGenerator
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.adapters.jobs import ADVANCE
from ledger_country_do.channels.ecf_v1.adapters.postgres.repositories.documents import PgDocuments
from ledger_country_do.channels.ecf_v1.adapters.postgres.repositories.ranges import PgRanges
from ledger_country_do.channels.ecf_v1.adapters.postgres.repositories.received import PgReceived
from ledger_country_do.channels.ecf_v1.adapters.postgres.repositories.submissions import PgSubmissions
from ledger_country_do.channels.ecf_v1.adapters.statuses import update_of
from ledger_country_do.channels.ecf_v1.application.documents import Advances
from ledger_country_do.channels.ecf_v1.application.ranges import (
    Announcements,
    Audit,
    NumberingUnit,
    NumberingUnits,
)
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_country_do.regime.tax_ids import CEDULA, RNC, RNC_DIGITS
from ledger_fiscal_kit.events import DocumentUpdated, ReceivedDocumentRecorded, RequirementsChanged
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tax_ids import TaxId
from ledger_kernel.tenancy import Tenant
lazy from ledger_country_do.channels.ecf_v1.application.documents import EcfDocument
lazy from ledger_country_do.channels.ecf_v1.application.ranges import AuditAction
lazy from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument
lazy from ledger_country_do.channels.ecf_v1.application.steps import ParkReason
lazy from ledger_platform.data import BoundDatabase, UnitOfWork


@final
class UnitAnnouncements(Announcements):
    """The channel's events, in the unit's outbox: they commit with the change or not at all (rule 3)."""

    __slots__ = ("_tenant", "_unit")

    def __init__(self, unit: UnitOfWork, tenant: Tenant) -> None:
        self._unit = unit
        self._tenant = tenant

    @override
    def requirements_changed(self) -> None:
        self._unit.publish(RequirementsChanged.of(self._tenant, channel=KEY))

    @override
    def document_updated(self, document: EcfDocument) -> None:
        self._unit.publish(DocumentUpdated.of(self._tenant, update_of(document)))

    @override
    def received(self, document: ReceivedDocument) -> None:
        issuer = document.ecf.issuer_rnc  # nine digits (an RNC) or eleven (a cédula), as it was read
        scheme = RNC.key if len(issuer) == RNC_DIGITS else CEDULA.key
        self._unit.publish(
            ReceivedDocumentRecorded(
                scope=self._tenant,
                subject=document.id,
                aggregate_version=document.version,
                received=document.id,
                channel=KEY,
                environment=document.environment.value,
                issuer=TaxId(scheme, issuer),
                number=str(document.ecf.encf),
            )
        )


@final
class UnitAdvances(Advances):
    """The ``do_ecf.advance`` jobs, in the unit's queue: enqueued with the document, or not at all.

    Woken for the unit's company, in its mode (the unit's scope), once the unit commits.
    """

    __slots__ = ("_tenant", "_unit")

    def __init__(self, unit: UnitOfWork, tenant: Tenant) -> None:
        self._unit = unit
        self._tenant = tenant

    @override
    def enqueue(self, document: UUID, *, at: datetime) -> None:
        self._unit.jobs.enqueue(ADVANCE, str(document), run_at=at)

    @override
    def wake(self, reason: ParkReason) -> None:
        self._unit.jobs.wake_all(ADVANCE, self._tenant.company, reason.value)


@final
class UnitAudit(Audit):
    """The channel's audit rows, in the unit's ``audit_events``: the acting call is the actor."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def record(self, action: AuditAction, subject: UUID, *, before: object, after: object) -> None:
        self._unit.audit(action=action, subject=subject, before=before, after=after)


def bind(unit: UnitOfWork) -> NumberingUnit:
    """The channel's view of ``unit``, which must be scoped to a company in a mode (a defect otherwise)."""
    scope = unit.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="unit.scope", requirement="a company in a mode")
    return NumberingUnit(
        tenant=scope,
        ranges=PgRanges(unit.connection, scope, scope.mode),
        documents=PgDocuments(unit.connection, scope, scope.mode),
        submissions=PgSubmissions(unit.connection, scope, scope.mode),
        received=PgReceived(unit.connection, scope, scope.mode),
        advances=UnitAdvances(unit, scope),
        announcements=UnitAnnouncements(unit, scope),
        audit=UnitAudit(unit),
        now=unit.now,
    )


@final
class PgUnits(NumberingUnits):
    """The units the use cases work in: the channel's own, in its schema and role, as ``bind`` sees them."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[NumberingUnit]:
        async with self._database.unit(tenant) as unit:
            yield bind(unit)
