"""The v2 unit of work (§5.3): one transaction, one module role, one tenant scope.

``BoundDatabase.unit(scope)`` opens it. Inside, the module changes its own rows through ``connection`` and
records what the transaction also decides: events (``publish``), jobs (``jobs``) and audit events (``audit``).
Records are validated at once and written when the block ends, before the commit, in the order they were made:
the commit makes them real with the module's changes or not at all, and the NOTIFY goes out after it. The unit
marks itself open on the task (``open_unit``) for the facade guard (§3.4, rule 13).
"""

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping
lazy from contextvars import Token
lazy from datetime import datetime, timedelta
lazy from uuid import UUID

from sqlalchemy import insert, update
from sqlalchemy.dialects.postgresql import insert as upsert
lazy from sqlalchemy import Executable
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import LedgerError
from ledger_kernel.ids import new_id
from ledger_platform.data.audit import current_actor
from ledger_platform.data.codec import MAX_PAYLOAD, canonical_json, encode, jsonb_size, payload_of
from ledger_platform.data.database import ClosedUnitOfWorkError
from ledger_platform.observability.tracing import current_traceparent
lazy from ledger_kernel.events import Event
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.tables import ChassisTables

type Callback = Callable[[], None]
"""Runs after the commit: a plain function that only schedules or signals."""


class EventTooLargeError(LedgerError):
    """An event's payload exceeds 64 KiB (§3.5): a defect of the producer."""

    code: ClassVar[str] = "event_too_large"

    def __init__(self, event_type: str, size: int) -> None:
        super().__init__(f"a {event_type} payload is {size} bytes, above {MAX_PAYLOAD}")


class UndeclaredEventTypeError(LedgerError):
    """A module published an event type its manifest does not declare (rule 11)."""

    code: ClassVar[str] = "event_type_undeclared"

    def __init__(self, event_type: str, module: str) -> None:
        super().__init__(f"{module} publishes {event_type}, which its manifest does not declare")


class UndeclaredJobKindError(LedgerError):
    """A module enqueued or woke a job kind its manifest does not declare."""

    code: ClassVar[str] = "job_kind_undeclared"

    def __init__(self, kind: str, module: str) -> None:
        super().__init__(f"{module} enqueues {kind}, which its manifest does not declare")


class MissingAuditScopeError(LedgerError):
    """``uow.audit`` in a unit without an organization: audit rows are an organization's (§6.3), a defect."""

    code: ClassVar[str] = "audit_scope_missing"

    def __init__(self) -> None:
        super().__init__("an audit event belongs to an organization: open the unit in a scope that names one")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class JobPolicy:
    """A job kind's limits, from the manifest."""

    max_attempts: int
    deadline: timedelta | None


_OPEN: Final[ContextVar[UnitOfWork | None]] = ContextVar("ledger_open_unit", default=None)


def open_unit() -> UnitOfWork | None:
    """The unit open on the current task, if any (a closed one inherited by a child task does not count)."""
    unit = _OPEN.get()
    return unit if unit is not None and unit.is_open else None


def _scope_columns(scope: Scope | None) -> dict[str, object]:
    if scope is None:
        return {"organization_id": None, "company_id": None, "mode": None}
    mode = scope.mode
    return {
        "organization_id": scope.organization,
        "company_id": scope.company,
        "mode": None if mode is None else mode.value,
    }


def _digest(value: object) -> bytes | None:
    return None if value is None else hashlib.sha256(canonical_json(encode(value))).digest()


@final
class UnitJobs:
    """The unit's jobs: enqueue, wake one, wake all parked ones of a company and reason (§3.6)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    def enqueue(
        self,
        kind: str,
        key: str,
        *,
        run_at: datetime | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """A job of ``kind`` keyed ``key`` at ``run_at`` (the unit's time by default), with its kind's limits.

        An existing job of that kind and key is left as it is while ready, running or parked, and restarted
        (attempts 0, the new payload) when done or dead.
        """
        unit = self._unit
        unit.require_open()
        policy = unit.policy_of(kind)
        jobs = unit.tables.jobs
        now = unit.now
        fresh = upsert(jobs).values(
            kind=kind,
            key=key,
            **_scope_columns(unit.scope),
            status="ready",
            run_at=run_at or now,
            max_attempts=policy.max_attempts,
            deadline_at=None if policy.deadline is None else now + policy.deadline,
            payload=encode(dict(payload or {})),
            traceparent=current_traceparent(),
            created_at=now,
            updated_at=now,
        )
        unit.record(
            fresh.on_conflict_do_update(
                index_elements=(jobs.c.kind, jobs.c.key),
                set_={
                    "status": "ready",
                    "attempts": 0,
                    "payload": fresh.excluded.payload,
                    "run_at": fresh.excluded.run_at,
                    "max_attempts": fresh.excluded.max_attempts,
                    "deadline_at": fresh.excluded.deadline_at,
                    "lease_until": None,
                    "lease_token": None,
                    "parked_reason": None,
                    "last_error_code": None,
                    "traceparent": fresh.excluded.traceparent,
                    "updated_at": fresh.excluded.updated_at,
                },
                where=jobs.c.status.in_(("done", "dead")),
            )
        )

    def wake(self, kind: str, key: str) -> None:
        """Make the parked job ``(kind, key)`` ready now; a job in any other state is left as it is."""
        unit = self._unit
        unit.require_open()
        unit.policy_of(kind)
        jobs = unit.tables.jobs
        unit.record(
            update(jobs)
            .where(jobs.c.kind == kind, jobs.c.key == key, jobs.c.status == "parked")
            .values(status="ready", run_at=unit.now, parked_reason=None, updated_at=unit.now)
        )

    def wake_all(self, kind: str, company: CompanyId, reason: str) -> None:
        """Make every job of ``kind`` parked for ``company`` with ``reason`` ready now."""
        unit = self._unit
        unit.require_open()
        unit.policy_of(kind)
        jobs = unit.tables.jobs
        unit.record(
            update(jobs)
            .where(
                jobs.c.kind == kind,
                jobs.c.company_id == company,
                jobs.c.status == "parked",
                jobs.c.parked_reason == reason,
            )
            .values(status="ready", run_at=unit.now, parked_reason=None, updated_at=unit.now)
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Binding:
    """What a module's units know of it: its tables, its name as a source, what it may publish and enqueue."""

    tables: ChassisTables
    source: str
    publishes: frozenset[str]
    policies: Mapping[str, JobPolicy]


@final
class UnitOfWork:
    """One open transaction (see the module). Made by ``BoundDatabase.unit`` only."""

    __slots__ = (
        "_after_commit",
        "_binding",
        "_open",
        "_pending",
        "_published",
        "connection",
        "jobs",
        "now",
        "scope",
        "tables",
    )

    def __init__(
        self,
        connection: AsyncConnection,
        *,
        scope: Scope | None,
        now: datetime,
        binding: Binding,
    ) -> None:
        self.connection = connection
        """For repositories: every statement runs inside the unit's transaction, role and scope."""
        self.scope = scope
        """Whose rows the unit may touch: row-level security enforces it, repositories name it too (§2)."""
        self.now = now
        """The unit's time, read once from the clock: every record of the unit carries it."""
        self.tables = binding.tables
        self.jobs = UnitJobs(self)
        self._binding = binding
        self._pending: list[Executable] = []
        self._after_commit: list[Callback] = []
        self._open = True
        self._published = False

    @property
    def is_open(self) -> bool:
        """Whether the transaction is still open (records are accepted)."""
        return self._open

    @property
    def published(self) -> bool:
        """Whether the unit recorded an event (its commit signals ``ledger_outbox``)."""
        return self._published

    def require_open(self) -> None:
        """``ClosedUnitOfWorkError`` once the transaction has ended: a record could never be written."""
        if not self._open:
            raise ClosedUnitOfWorkError

    def record(self, statement: Executable) -> None:
        """Keep ``statement`` to run before the commit (the chassis's own writers use it)."""
        self.require_open()
        self._pending.append(statement)

    def policy_of(self, kind: str) -> JobPolicy:
        """``kind``'s policy; ``UndeclaredJobKindError`` if the manifest does not declare it."""
        policy = self._binding.policies.get(kind)
        if policy is None:
            raise UndeclaredJobKindError(kind, self._binding.source)
        return policy

    def publish(self, event: Event) -> None:
        """Write ``event`` to the module's outbox when the unit commits (rule 3)."""
        self.require_open()
        if event.TYPE not in self._binding.publishes:
            raise UndeclaredEventTypeError(event.TYPE, self._binding.source)
        payload = payload_of(event)
        size = jsonb_size(payload)
        if size > MAX_PAYLOAD:
            raise EventTooLargeError(event.TYPE, size)
        self.record(
            insert(self.tables.outbox).values(
                id=new_id(),
                type=event.TYPE,
                version=event.VERSION,
                source=self._binding.source,
                **_scope_columns(event.scope),
                subject=event.subject,
                aggregate_version=event.aggregate_version,
                occurred_at=self.now,
                traceparent=current_traceparent(),
                payload=payload,
            )
        )
        self._published = True

    def audit(
        self,
        *,
        action: str,
        subject: UUID | str,
        before: object = None,
        after: object = None,
        detail: Mapping[str, str | int] | None = None,
    ) -> None:
        """Append who did ``action`` to ``subject``, with hashes of the canonical before and after (§6.4).

        The actor is the bound call's (``acting_as``); the organization is the unit's, which it must have.
        """
        self.require_open()
        actor = current_actor()
        if self.scope is None:
            raise MissingAuditScopeError
        self.record(
            insert(self.tables.audit_events).values(
                id=new_id(),
                occurred_at=self.now,
                **_scope_columns(self.scope),
                actor=actor.name,
                call_id=actor.call_id,
                action=action,
                subject_type=action.partition(".")[0],
                subject_id=str(subject),
                before_hash=_digest(before),
                after_hash=_digest(after),
                detail=encode(dict(detail or {})),
            )
        )

    def after_commit(self, callback: Callback) -> None:
        """Run ``callback`` once the transaction has committed; never after a rollback.

        Callbacks run in the order added, once; one that raises is logged and the others still run.
        """
        self.require_open()
        self._after_commit.append(callback)

    async def flush(self) -> None:
        """Write what was recorded, in order (``BoundDatabase.unit`` calls it before the commit)."""
        for statement in self._pending:
            await self.connection.execute(statement)
        self._pending.clear()

    def close(self) -> tuple[Callback, ...]:
        """Take no more records; the after-commit callbacks, for the caller to run if it committed."""
        self._open = False
        self._pending.clear()
        callbacks = tuple(self._after_commit)
        self._after_commit.clear()
        return callbacks

    def mark_open(self) -> Token[UnitOfWork | None]:
        """Make this the task's open unit; the token ``unmark`` takes."""
        return _OPEN.set(self)

    @staticmethod
    def unmark(token: Token[UnitOfWork | None]) -> None:
        """Undo ``mark_open``."""
        _OPEN.reset(token)
