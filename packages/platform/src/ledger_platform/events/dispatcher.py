"""The fan-out (§3.5): outbox rows of every schema into the inboxes of their consumers, as ``ledger_relay``.

One transaction per schema and pass: claim up to ``batch`` outbox rows (``FOR UPDATE SKIP LOCKED``, oldest
first), insert one inbox row per subscribed consumer (``ON CONFLICT DO NOTHING``: a repeat is absorbed; the
inserts are cut under PostgreSQL's 65 535 parameters), delete the outbox rows; rows nobody subscribes to are
kept and marked ``unrouted_since`` (counted, alerted), never deleted. A schema is fanned out by one dispatcher
at a time (a transaction-level advisory lock; a dispatcher that finds it held skips the schema this pass), so
a schema's events reach the inboxes in the order of their ids.

A pass whose batch fails is done again one row at a time, each in a savepoint of the pass (which keeps the
schema's lock), paging on past the rows that fail until a batch's worth went: the good rows go, even behind a
batch's worth of rows a broken target refuses. Such a pass looks at ``SCAN_BATCHES`` batches' worth of rows at
most, and a pass that skipped rows through no fault of theirs leaves a mark in memory: the schema's next
row-by-row pass resumes after the last row it looked at, so a broken target's backlog costs each pass a
bounded scan, never the whole backlog again. The mark is dropped by the first pass that skips none (the one
after it starts over from the oldest row) and by every new routing version. A row below the mark that becomes
due only later (an older id committed late by a long transaction or another process, or a row whose
``attempts`` an operator reset) waits for that wrap-around: a bounded delay, at most the passes it takes to
walk the rest of the backlog. ``ledger_outbox_skipped{schema}`` is how many rows the schema's last pass
skipped so. A row that still fails is blamed (its ``attempts``
counted, ``outbox.fan_out_failed``, ``ledger_outbox_failed_total``) only when the error is its own: it could
not be read, broke a constraint, or a trigger refused it. Any other error (a revoked grant on one target
inbox, a lost connection) is no row's fault: such a row is skipped uncounted, whatever the rows beside it did,
and when every row of the pass failed that way the pass is rolled back and fails. A failed pass is logged
(``outbox.pass_failed``, with the error's type) and counted (``ledger_outbox_pass_failed_total{schema}``), and
the other schemas still go; only when every schema's pass failed is the error raised, for the supervisor to
back off. At ``MAX_FAN_OUT_ATTEMPTS`` a row is set aside, kept and alerted, until an operator resets its
attempts. One poison row never holds back the rest, and a failure is never progress: a drain ends when nothing
more goes through.

The routing table is cached and reloaded when ``platform.routing.version`` moves (one read per pass, in a
short transaction of its own); every load makes the unrouted rows of the types it routes due again, so a row
marked by a dispatcher whose table was a version behind is fanned out at that dispatcher's next pass, or by
whichever dispatcher starts next. ``ledger_outbox_unrouted_total`` counts markings, not rows: a row marked
again after a reload is counted again. Every commit that moved rows signals ``ledger_inbox``.

Times are the database's: ``unrouted_since`` is the relay transaction's ``now()``, and an inbox row is due
from its insert (the column's default).
"""

from contextlib import aclosing
from dataclasses import dataclass
from typing import ClassVar, Final, Never, final
from uuid import UUID
lazy from collections.abc import AsyncGenerator, Iterator, Sequence
lazy from datetime import datetime

import structlog
from psycopg.errors import RaiseException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, SQLAlchemyError
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import ColumnElement
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import LedgerError, error_type
from ledger_platform.data.notifier import INBOX, OUTBOX
from ledger_platform.data.queue import being_cancelled
from ledger_platform.data.tables import chassis_tables
from ledger_platform.events.routing import load_routing, routing_version
from ledger_platform.observability.metrics import counter, gauge
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.data.database import AnyTable, Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.events.routing import Routing
lazy from ledger_platform.events.wakeups import Waker

_RELAY: Final = text("SET LOCAL ROLE ledger_relay")
_LOCK: Final = text("SELECT pg_try_advisory_xact_lock(hashtext('ledger.fan_out'), hashtext(:schema))")
"""One fan-out per schema at a time, for the length of the pass's transaction."""
_COPIED: Final = (
    "type",
    "version",
    "source",
    "organization_id",
    "company_id",
    "mode",
    "subject",
    "aggregate_version",
    "occurred_at",
    "traceparent",
    "payload",
)
POLL: Final = 1.0
"""Seconds between passes when no NOTIFY arrives (§3.5's safety net)."""
MAX_FAN_OUT_ATTEMPTS: Final = 10
"""Failed fan-outs after which a row is set aside (kept, alerted) until an operator resets its attempts."""
SCAN_BATCHES: Final = 10
"""Batches' worth of rows a row-by-row pass looks at, at most."""
ROWS_PER_INSERT: Final = 65_535 // (len(_COPIED) + 2)
"""Inbox rows per ``INSERT``: each binds its consumer, its event id and the envelope."""
_FAILURES: Final = (SQLAlchemyError, ValidationError)
"""What fanning out a row can fail with: the database refused it, or the row could not be read."""
_BOOL: Final = TypeAdapter(bool)
_IDS: Final = TypeAdapter(list[UUID])
_SET_ASIDE: Final = TypeAdapter(tuple[int, str])
_logger = structlog.stdlib.get_logger(__name__)


class NoSchemasError(LedgerError):
    """A dispatcher given no schema to fan out: a defect of the app's assembly."""

    code: ClassVar[str] = "dispatcher_schemas_missing"

    def __init__(self) -> None:
        super().__init__("a dispatcher was given no schema to fan out")


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """``items`` in consecutive slices of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _OutboxRow:
    """An outbox row's id and envelope, as the fan-out copies them."""

    id: UUID
    type: str
    version: int
    source: str
    organization_id: UUID | None
    company_id: UUID | None
    mode: str | None
    subject: UUID | None
    aggregate_version: int
    occurred_at: datetime
    traceparent: str | None
    payload: dict[str, JsonValue]

    def delivery(self, consumer: str) -> dict[str, object]:
        """The inbox row of ``consumer`` for this event."""
        return {
            "consumer": consumer,
            "event_id": self.id,
            "type": self.type,
            "version": self.version,
            "source": self.source,
            "organization_id": self.organization_id,
            "company_id": self.company_id,
            "mode": self.mode,
            "subject": self.subject,
            "aggregate_version": self.aggregate_version,
            "occurred_at": self.occurred_at,
            "traceparent": self.traceparent,
            "payload": self.payload,
        }


_ROWS: Final = TypeAdapter(list[_OutboxRow])


@final
@dataclass(frozen=True, slots=True)
class _Moved:
    """What one transaction did with the rows it took."""

    taken: int
    routed: int
    unrouted: int


@final
@dataclass(frozen=True, slots=True)
class _Failure:
    """A row whose fan-out failed, as its log line names it (the error by its type only)."""

    event_id: UUID
    event_type: str
    attempts: int
    error: str


def _row_specific(error: Exception) -> bool:
    """Whether ``error`` is the row's own fault.

    It could not be read, broke a constraint or a value's type, or a trigger refused it. Any other error (a
    privilege, the connection) would fail every row alike.
    """
    return isinstance(error, (ValidationError, IntegrityError, DataError)) or (
        isinstance(error, DBAPIError) and isinstance(error.orig, RaiseException)
    )


def reraise_when_stopping(error: Exception) -> None:
    """Raise ``error`` (the one being handled) again when the task is being stopped.

    A clean-up (a rollback on a broken connection) can turn a cancellation into another error; a loop that
    carries on after errors must end on it all the same.
    """
    if being_cancelled():
        raise error


def _due(outbox: AnyTable) -> tuple[ColumnElement[bool], ...]:
    """Rows to fan out: neither unrouted nor set aside."""
    return (outbox.c.unrouted_since.is_(None), outbox.c.attempts < MAX_FAN_OUT_ATTEMPTS)


@final
class Dispatcher:
    """See the module."""

    __slots__ = (
        "_batch",
        "_database",
        "_failed",
        "_passes_failed",
        "_resume",
        "_routing",
        "_schemas",
        "_signals",
        "_skipped",
        "_unrouted",
    )

    def __init__(
        self,
        database: Database,
        *,
        schemas: Sequence[str],
        signals: Signals,
        batch: int = 100,
        registry: CollectorRegistry | None = None,
    ) -> None:
        if not schemas:
            raise NoSchemasError
        self._database = database
        self._schemas = tuple(schemas)
        self._signals = signals
        self._batch = batch
        self._routing: Routing | None = None
        self._resume: dict[str, UUID] = {}
        """Per schema, the row after which the next row-by-row pass resumes (see the module)."""
        self._skipped = gauge(
            registry,
            "ledger_outbox_skipped",
            "Rows the schema's last pass skipped through no fault of theirs (a broken target).",
            ["schema"],
        )
        self._unrouted = counter(
            registry, "ledger_outbox_unrouted", "Events marked unrouted: no consumer subscribed.", ["schema"]
        )
        self._failed = counter(registry, "ledger_outbox_failed", "Failed fan-outs of one event.", ["schema"])
        self._passes_failed = counter(
            registry,
            "ledger_outbox_pass_failed",
            "Fan-out passes of a schema that failed as a whole.",
            ["schema"],
        )
        for schema in self._schemas:  # exported at zero: a first event must show in increase()
            self._unrouted.labels(schema=schema)
            self._failed.labels(schema=schema)
            self._passes_failed.labels(schema=schema)
            self._skipped.labels(schema=schema).set(0)

    async def _current(self) -> Routing:
        """The routing table, reloaded when its version moved; a load makes newly routed rows due again."""
        async with self._database.engine.begin() as connection:
            await connection.execute(_RELAY)
            cached = self._routing
            if cached is not None and cached.version == await routing_version(connection):
                return cached
            fresh = await load_routing(connection, self._schemas)
            if fresh.routes:
                await self._reroute(connection, tuple(fresh.routes))
        self._routing = fresh  # only once committed: a failed reroute is tried again at the next pass
        self._resume.clear()  # the routes changed: a target that was skipped may be one no longer
        return fresh

    async def _reroute(self, connection: AsyncConnection, types: tuple[str, ...]) -> None:
        for schema in self._schemas:
            outbox = chassis_tables(schema).outbox
            await connection.execute(
                update(outbox)
                .where(outbox.c.unrouted_since.is_not(None), outbox.c.type.in_(types))
                .values(unrouted_since=None)
            )

    @staticmethod
    async def _enter(connection: AsyncConnection, schema: str) -> bool:
        """Become the relay and take ``schema``'s fan-out lock; whether it was free."""
        await connection.execute(_RELAY)
        return _BOOL.validate_python((await connection.execute(_LOCK, {"schema": schema})).scalar_one())

    @staticmethod
    async def _deliver(
        connection: AsyncConnection,
        outbox: AnyTable,
        routing: Routing,
        chosen: ColumnElement[bool],
        limit: int,
    ) -> _Moved:
        """Take the ``chosen`` due rows (at most ``limit``) and fan them out in the transaction."""
        due = (
            select(outbox.c.id, *(outbox.c[column] for column in _COPIED))
            .where(*_due(outbox), chosen)
            .order_by(outbox.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = _ROWS.validate_python([dict(row) for row in (await connection.execute(due)).mappings()])
        deliveries: dict[str, list[dict[str, object]]] = {}
        routed: list[UUID] = []
        unrouted: list[UUID] = []
        for row in rows:
            routes = routing.routes.get(row.type, ())
            (routed if routes else unrouted).append(row.id)
            for route in routes:
                deliveries.setdefault(route.schema, []).append(row.delivery(route.consumer))
        for target, values in deliveries.items():
            inbox = chassis_tables(target).inbox
            for chunk in chunked(values, ROWS_PER_INSERT):
                await connection.execute(insert(inbox).values(list(chunk)).on_conflict_do_nothing())
        if routed:
            await connection.execute(delete(outbox).where(outbox.c.id.in_(routed)))
        if unrouted:
            await connection.execute(
                update(outbox).where(outbox.c.id.in_(unrouted)).values(unrouted_since=func.now())
            )
        return _Moved(len(rows), len(routed), len(unrouted))

    def _committed(self, schema: str, moved: _Moved, failures: Sequence[_Failure]) -> None:
        """After the commit: count, log and signal what the pass did."""
        if moved.unrouted:
            self._unrouted.labels(schema=schema).inc(moved.unrouted)
        for failure in failures:
            _logger.warning(
                "outbox.fan_out_failed",
                schema=schema,
                event_id=str(failure.event_id),
                event_type=failure.event_type,
                attempts=failure.attempts,
                error=failure.error,
            )
            self._failed.labels(schema=schema).inc()
        if moved.routed:
            self._signals.signal(INBOX)

    async def _pass(self, schema: str, routing: Routing) -> int:
        """One transaction over ``schema``'s due rows; row by row if the batch fails (see the module).

        The batch and each row run in a savepoint of the pass's transaction, so the schema's lock is held
        from the first row to the last, and a failure's count commits with the rows that went.
        """
        outbox = chassis_tables(schema).outbox
        failures: list[_Failure] = []
        async with self._database.engine.begin() as connection:
            if not await self._enter(connection, schema):
                return 0
            try:
                async with connection.begin_nested():
                    moved = await self._deliver(
                        connection, outbox, routing, outbox.c.id.is_not(None), self._batch
                    )
            except _FAILURES as error:
                reraise_when_stopping(error)
                moved = await self._one_by_one(connection, schema, routing, failures)
            else:
                self._resume.pop(schema, None)
                self._skipped.labels(schema=schema).set(0)
        self._committed(schema, moved, failures)
        return moved.taken

    async def _due_ids(
        self, connection: AsyncConnection, outbox: AnyTable, start: UUID | None
    ) -> AsyncGenerator[UUID]:
        """The due rows' ids after ``start`` (all, without it), oldest first, a batch's worth per query."""
        after: ColumnElement[bool] = outbox.c.id.is_not(None) if start is None else outbox.c.id > start
        while True:
            page = select(outbox.c.id).where(*_due(outbox), after).order_by(outbox.c.id).limit(self._batch)
            ids = _IDS.validate_python((await connection.execute(page)).scalars().all())
            for event_id in ids:
                yield event_id
            if len(ids) < self._batch:
                return
            after = outbox.c.id > ids[-1]

    async def _one_by_one(
        self, connection: AsyncConnection, schema: str, routing: Routing, failures: list[_Failure]
    ) -> _Moved:
        """Each due row in a savepoint of its own, past the rows that fail, until a batch's worth went.

        Paging on past the skipped rows keeps a batch of rows for a broken target (an inbox that refuses the
        relay) from holding back the rows of healthy targets behind it; the scan is bounded, and resumes where
        the last one that skipped rows stopped (see the module).
        """
        outbox = chassis_tables(schema).outbox
        limit = SCAN_BATCHES * self._batch
        taken = routed = unrouted = scanned = skipped = 0
        failed: list[tuple[UUID, Exception]] = []
        async with aclosing(self._due_ids(connection, outbox, self._resume.get(schema))) as due:
            async for event_id in due:
                scanned += 1
                try:
                    async with connection.begin_nested():
                        moved = await self._deliver(connection, outbox, routing, outbox.c.id == event_id, 1)
                except _FAILURES as error:
                    reraise_when_stopping(error)
                    failed.append((event_id, error))
                    skipped += not _row_specific(error)
                else:
                    taken, routed, unrouted = (
                        taken + moved.taken,
                        routed + moved.routed,
                        unrouted + moved.unrouted,
                    )
                if skipped:
                    self._resume[schema] = event_id
                if taken >= self._batch or scanned >= limit:
                    break
        if not skipped:
            self._resume.pop(schema, None)
        self._skipped.labels(schema=schema).set(skipped)
        systemic = [cause for _, cause in failed if not _row_specific(cause)]
        if systemic and len(systemic) == len(failed) and not taken:  # nothing went, through no row's fault
            raise systemic[0]
        for event_id, cause in failed:
            if not _row_specific(cause):
                continue  # not the row's fault (its target refuses the relay): skipped, never blamed
            counted = (
                update(outbox)
                .where(outbox.c.id == event_id)
                .values(attempts=outbox.c.attempts + 1)
                .returning(outbox.c.attempts, outbox.c.type)
            )
            attempts, event_type = _SET_ASIDE.validate_python(
                tuple((await connection.execute(counted)).one())
            )
            failures.append(_Failure(event_id, event_type, attempts, error_type(cause)))
        return _Moved(taken, routed, unrouted)

    async def run_once(self) -> int:
        """One pass over every schema; how many outbox rows went out or were marked (a failure is none)."""
        routing = await self._current()
        taken = 0
        failed: list[Exception] = []
        for schema in self._schemas:
            try:
                taken += await self._pass(schema, routing)
            except _FAILURES as error:  # this schema's pass: the others still go
                reraise_when_stopping(error)
                _logger.warning("outbox.pass_failed", schema=schema, error=error_type(error))
                self._passes_failed.labels(schema=schema).inc()
                failed.append(error)
        if len(failed) == len(self._schemas):  # nothing works: for the supervisor to back off
            raise failed[0]
        return taken

    async def run_until_idle(self) -> int:
        """Pass until a pass finds nothing (tests drain with it: absence is proved by draining, §14.2)."""
        total = 0
        while handled := await self.run_once():
            total += handled
        return total

    async def run(self, wakeups: Waker) -> Never:
        """Forever: drain, then wait for a NOTIFY on ``ledger_outbox`` or the poll (the supervised loop)."""
        woken = wakeups.subscribe(OUTBOX)
        while True:
            await self.run_until_idle()
            await wakeups.wait(woken, poll=POLL)
