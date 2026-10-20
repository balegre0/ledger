"""signer's unit of work over a chassis unit: its certificates, events, audit rows, jobs and import keys.

``bind(uow)`` is the signer view of any unit with an organization: the units the use cases open
(``PgUnits.unit``), and the one the platform opens around a consumed event's inbox row, so that what the
consumer changes commits with the row's deletion. Everything the view records goes through the chassis unit,
so it commits with the unit's changes or not at all.

A second active certificate for a mode is ``ActiveCertificateTakenError`` wherever the partial unique index
refuses it: at the statement (the repository maps it) or when the unit writes what it recorded, as its block
ends (mapped here); the import then tries its unit again once.
"""

from contextlib import asynccontextmanager
from typing import ClassVar, Final, final, override
lazy from collections.abc import AsyncGenerator, Mapping
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import LedgerError
from ledger_platform.http.idempotency import Idempotency
from ledger_signer.adapters.postgres.import_keys import PgImportKeys
from ledger_signer.adapters.postgres.repositories import PgCertificates, taken_as_raced
from ledger_signer.application.ports import (
    Audit,
    Events,
    Jobs,
    SignerUnit,
    SignerUnits,
)
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.data import BoundDatabase, UnitOfWork
lazy from ledger_signer.application.ports import AuditAction, JobKind
lazy from ledger_signer.events import CertificateEvent

_KEYS: Final = Idempotency()
"""Import keys kept the platform's seven days."""


class SignerUnitUnscopedError(LedgerError):
    """signer's state asked of a unit scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "signer_unit_unscoped"

    def __init__(self) -> None:
        super().__init__("signer's state is read and changed in a unit scoped to an organization")


@final
class OutboxEvents(Events):
    """signer's events, in the unit's outbox."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def publish(self, event: CertificateEvent) -> None:
        self._unit.publish(event)


@final
class AuditTrail(Audit):
    """signer's audit rows, in the unit's ``audit_events``: the acting call is the actor."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        detail: Mapping[str, str | int],
        before: object = None,
        after: object = None,
    ) -> None:
        self._unit.audit(action=action, subject=subject, before=before, after=after, detail=detail)


@final
class QueuedJobs(Jobs):
    """signer's jobs, in the unit's ``jobs``, scoped as the unit is."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def enqueue(self, kind: JobKind, key: str, *, run_at: datetime, payload: Mapping[str, str | int]) -> None:
        self._unit.jobs.enqueue(kind, key, run_at=run_at, payload=payload)


def bind(uow: UnitOfWork) -> SignerUnit:
    """The signer view of ``uow``: its organization's repositories, its records, its time (the database's)."""
    scope = uow.scope
    if scope is None:
        raise SignerUnitUnscopedError
    return SignerUnit(
        certificates=PgCertificates(uow.connection, scope.organization),
        events=OutboxEvents(uow),
        audit=AuditTrail(uow),
        jobs=QueuedJobs(uow),
        import_keys=PgImportKeys(uow, _KEYS),
        now=uow.now,
    )


@final
class PgUnits(SignerUnits):
    """The units the use cases work in: signer's own, in its schema and role, as ``bind`` sees them."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[SignerUnit]:
        with taken_as_raced():
            async with self._database.unit(tenant) as uow:
                yield bind(uow)
