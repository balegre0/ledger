"""The inbox runner (§3.5): one consumer's rows, claimed with a lease and handled exactly once in effect.

A claim, in a short unit of its own, first recovers the consumer's rows whose lease ran out (``ready`` a
backoff later, ``inbox_worker_recovered``, or ``dead`` with ``worker_lost`` once the attempts are spent,
``inbox_worker_lost``), then takes due ``ready`` rows of the consumer's own types, counts the attempt, and
sets a fencing token.

Each row is handled in its own unit, in the consumer's module role and the event's scope. The unit's first
statement extends the lease, fenced by the token (``WHERE lease_token = :token``): if it finds nothing,
another worker took the row over and the handler does not run (``inbox_lease_lost``); if it does, the unit
holds the row's lock until it ends, so no other worker can take the row while the handler works, however long
that takes. The handler runs in a savepoint of the unit: its writes and the deletion of the row commit
together. If it fails, the savepoint is undone (its writes and what it recorded) and the failure is recorded
in the same unit, still under the row's lock, with the error's own code: retried with backoff, or ``dead``
(kept, replayable) when the error is ``INVALID`` or the attempts are spent.

An *outside* consumer's handler runs with no unit open (it may call other modules' facades, rule 13) and so
cannot hold the row's lock: it holds the row by its lease instead. Its runner first extends the lease, fenced
by the token, in a short unit (finding nothing, the handler does not run: ``inbox_lease_lost``); renews it
every third of its length while the handler runs (``data.queue.renewing``: a renewal that finds the row taken
over, or fails, stops the handler, ``inbox_lease_lost``); then deletes the row, or records its failure by the
same rule as above, in one more unit whose statement names the token. A runner whose lease was taken over
meanwhile deletes and records nothing (``inbox_lease_lost``): the row is its new holder's. A handler that
runs past its consumer's ``timeout`` is stopped and its run fails as ``InboxHandlerTimedOutError`` (retried,
or dead once its attempts are spent), so a hung call cannot hold the consumer and renew its lease for ever;
a stopping runner thus waits for it at most that long. Its effects are
therefore at least once (a crash after the handler runs it again), never recorded twice; what it calls is
idempotent by the event or a version (rule 4). It is never ordered (the manifest refuses it).

An ordered consumer claims only the first row of each subject that is not dead, in the order
(``aggregate_version``, event id), a producer that gave no version having stored 0. The heads are read once
per claim from ``inbox_head`` (``DISTINCT ON (subject)``), never with a look back per row. Known limit: that
read costs O(the consumer's live rows) per claim (PostgreSQL may sort them once), so a backlog of many
thousands of live rows makes every claim pay for all of them; the fix, when a consumer needs it, is a skip
scan (a recursive CTE that jumps from one subject's head to the next through ``inbox_head``). Consequences
(Decision 18): a dead row no longer holds back its subject, so the rows behind it run before it, and so would
a replay of it; a replay is therefore refused while an earlier row of its subject is still live. Events
without a subject are not ordered (they have no subject to queue behind).

Rows of a type the running code has no handler for (a newer version's, after a rollback) are never claimed:
they wait, as many as the fan-out delivers while the subscription stays active, for the version that handles
them. Nothing else bounds them; ``ledger_inbox_unhandled_oldest_seconds`` (the age of the oldest, measured
once per drain) is what an alert watches, and an ordered subject whose head is one of them waits behind it.

A runner asked to ``stop`` claims nothing more: it finishes the row it is handling, gives the rest of its
batch back unspent (``ready``, the attempt returned) and sets ``drained``. A runner cancelled mid-batch gives
its rows back too, in a short shielded unit; the row whose handler it stopped keeps its attempt. A stopping
worker never leaves rows to wait out their leases.

Every time is the database's (§3.6): due rows and expired leases are compared with the claim unit's ``now()``;
a lease extension and a retry run from the statement that writes them (``statement_timestamp()``).
"""

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import ClassVar, Final, Never, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import Awaitable, Callable, Sequence
lazy from contextlib import AbstractContextManager
lazy from datetime import datetime
lazy from uuid import UUID

import structlog
from opentelemetry.trace import Status, StatusCode
from pydantic import TypeAdapter
from sqlalchemy import and_, delete, exists, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on
lazy from opentelemetry.trace import Span
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import ColumnElement, RowMapping, Update

from ledger_kernel.errors import ConflictError, ErrorKind, LedgerError, UnavailableError, error_type
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.notifier import INBOX
from ledger_platform.data.queue import (
    DEFAULT_BACKOFF,
    RELEASE_TIMEOUT,
    claim,
    parked,
    recover,
    release,
    renewing,
)
from ledger_platform.events.consumers import Outside, raw_event_of
from ledger_platform.events.dispatcher import POLL, reraise_when_stopping
from ledger_platform.observability.metrics import counter, gauge
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.data.queue import Backoff
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import Consumer, Handler, OutsideHandler, RawEvent
lazy from ledger_platform.events.wakeups import Waker
lazy from ledger_platform.observability.tracing import Tracing

OUTCOMES: Final = ("done", "retry", "dead", "lease_lost")
"""The values of ``ledger_inbox_handled_total``'s ``outcome`` label."""
DEFAULT_LEASE: Final = timedelta(seconds=60)
DEFAULT_BATCH: Final = 20
_NOTIFY: Final = text("SELECT pg_notify('ledger_inbox', '')")
_AGE: Final = TypeAdapter(float)
_ROW: Final = TypeAdapter(dict[str, object])
_BLOCKED: Final = TypeAdapter(bool)
_logger = structlog.stdlib.get_logger(__name__)


class _LeaseLostError(LedgerError):
    """The row was taken over while its handler ran: the unit must roll back (caught by the runner)."""

    code: ClassVar[str] = "inbox_lease_lost"

    def __init__(self) -> None:
        super().__init__("the inbox row's lease was taken over by another worker")


class InboxHandlerTimedOutError(UnavailableError):
    """An outside handler ran past its consumer's ``timeout``: the run is stopped and retried."""

    code: ClassVar[str] = "inbox_handler_timed_out"

    def __init__(self, consumer: str) -> None:
        super().__init__(f"{consumer}'s handler ran past its timeout")


class ReplayRefusedError(ConflictError):
    """A replay that would run an ordered consumer's row before an earlier live row of its subject.

    Replay it once those have run.
    """

    code: ClassVar[str] = "inbox_replay_refused"

    def __init__(self, consumer: str) -> None:
        super().__init__(f"{consumer} still has an earlier live event of this subject: replay after it")


class RunnerOptions(TypedDict):
    """How an inbox runner claims and records; each is optional."""

    lease: NotRequired[timedelta]
    """How long a claimed row is the runner's (60 s by default)."""
    batch: NotRequired[int]
    """At most this many rows per claim (20 by default)."""
    backoff: NotRequired[Backoff]
    """The waits between attempts (§3.5's by default)."""
    tracing: NotRequired[Tracing | None]
    """A span per handled event, continuing its trace; none by default."""
    registry: NotRequired[CollectorRegistry | None]
    """Where ``ledger_inbox_handled_total`` is exported; nowhere by default."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Lease:
    lease_token: UUID
    attempts: int
    next_attempt_at: datetime


_LEASE: Final = TypeAdapter(_Lease)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedEvent:
    """A claimed inbox row: the event, the fencing token that must still be the row's, the attempt number."""

    event: RawEvent
    lease_token: UUID
    attempts: int
    next_attempt_at: datetime


def _claimed(row: RowMapping) -> ClaimedEvent:
    mapping = _ROW.validate_python(row)
    lease = _LEASE.validate_python(mapping)
    return ClaimedEvent(
        event=raw_event_of(mapping),
        lease_token=lease.lease_token,
        attempts=lease.attempts,
        next_attempt_at=lease.next_attempt_at,
    )


def _earlier_live(inbox: AnyTable) -> ColumnElement[bool]:
    """Whether a live row of the same consumer and subject comes before ``inbox``'s row (see the module).

    The order is (``aggregate_version``, event id); a row without a subject has none before it
    (``NULL = NULL`` is not true). One probe of ``inbox_head`` per row asked about: for a replay, not claims.
    """
    older = inbox.alias("older")
    return exists().where(
        older.c.consumer == inbox.c.consumer,
        older.c.subject == inbox.c.subject,
        tuple_(older.c.aggregate_version, older.c.event_id)
        < tuple_(inbox.c.aggregate_version, inbox.c.event_id),
        older.c.status != "dead",
    )


def claimable(inbox: AnyTable, consumer: Consumer) -> ColumnElement[bool]:
    """The rows a claim of ``consumer`` may take (see the module).

    Due ``ready`` rows of its types; for an ordered consumer only those without a subject or at its head.
    """
    due = and_(
        inbox.c.consumer == consumer.name,
        inbox.c.type.in_(sorted(consumer.types)),
        inbox.c.status == "ready",
        inbox.c.next_attempt_at <= func.now(),
    )
    if not consumer.ordered:
        return due
    live = inbox.alias("live")
    heads = (
        select(live.c.event_id)
        .where(live.c.consumer == consumer.name, live.c.status != "dead", live.c.subject.is_not(None))
        .order_by(live.c.subject, live.c.aggregate_version, live.c.event_id)
        .ext(distinct_on(live.c.subject))
    )
    return and_(due, or_(inbox.c.subject.is_(None), inbox.c.event_id.in_(heads)))


@final
@dataclass(frozen=True, slots=True)
class _Failure:
    """How a failed handling is recorded."""

    code: str
    dead: bool
    error: str
    """The error's type (never its message)."""


def _fence(deleted: object) -> None:
    """``_LeaseLostError`` unless the fenced deletion returned the row (it is still this runner's)."""
    if deleted is None:
        raise _LeaseLostError


def _actor(consumer: str, event: RawEvent) -> Actor:
    """Who acts while ``consumer`` handles ``event``: what ``SystemCall(reason="consumer:<name>")`` names."""
    return Actor(f"system:consumer:{consumer}", str(event.id))


def _failed(span: Span | None, failure: _Failure) -> None:
    """Mark ``span`` failed with the error's type only: a message can hold data, and spans leave."""
    if span is not None:
        span.set_status(Status(StatusCode.ERROR))
        span.add_event("exception", {"exception.type": failure.error})


@final
class InboxRunner:
    """See the module."""

    __slots__ = (
        "_backoff",
        "_batch",
        "_consumer",
        "_database",
        "_handled",
        "_lease",
        "_recovered",
        "_stopping",
        "_tracing",
        "_waiting",
        "_woken",
        "drained",
    )

    def __init__(
        self, *, database: BoundDatabase, consumer: Consumer, **options: Unpack[RunnerOptions]
    ) -> None:
        self._database = database
        self._consumer = consumer
        self._lease = options.get("lease", DEFAULT_LEASE)
        self._batch = options.get("batch", DEFAULT_BATCH)
        self._backoff = options.get("backoff", DEFAULT_BACKOFF)
        self._tracing = options.get("tracing")
        self._stopping = asyncio.Event()
        self._woken: asyncio.Event | None = None
        self.drained: Final = asyncio.Event()
        """Set once a stopped runner has finished its row and given the rest back."""
        registry = options.get("registry")
        self._handled = counter(
            registry,
            "ledger_inbox_handled",
            "Inbox rows handled, by consumer and outcome.",
            ["consumer", "outcome"],
        )
        for outcome in OUTCOMES:
            self._handled.labels(consumer=consumer.name, outcome=outcome)  # exported at zero
        self._recovered = counter(
            registry,
            "ledger_inbox_worker_recovered",
            "Inbox rows whose lease ran out (their worker died), made ready again after the backoff.",
            ["consumer"],
        )
        self._recovered.labels(consumer=consumer.name)  # exported at zero
        self._waiting = gauge(
            registry,
            "ledger_inbox_unhandled_oldest_seconds",
            "Age of the oldest ready row of a type the consumer has no handler for.",
            ["consumer"],
        )

    async def claim_now(self) -> Sequence[ClaimedEvent]:
        """Claim the due rows (see the module), oldest first; ``run_once`` handles them (a test seam too)."""
        inbox = self._database.tables.inbox
        keys = ("consumer", "event_id")
        async with self._database.unit(None) as unit:
            recovered = await recover(
                unit.connection,
                inbox,
                keys=keys,
                group=inbox.c.consumer == self._consumer.name,
                due_column="next_attempt_at",
                max_attempts=self._consumer.max_attempts,
                backoff=self._backoff,
            )
            rows = await claim(
                unit.connection,
                inbox,
                keys=keys,
                due=claimable(inbox, self._consumer),
                order_by=(inbox.c.next_attempt_at,),
                limit=self._batch,
                lease=self._lease,
            )
        name = self._consumer.name
        if recovered.dead:
            _logger.warning("inbox_worker_lost", consumer=name, rows=recovered.dead)
            self._handled.labels(consumer=name, outcome="dead").inc(recovered.dead)
        if recovered.ready:
            _logger.warning("inbox_worker_recovered", consumer=name, rows=recovered.ready)
            self._recovered.labels(consumer=name).inc(recovered.ready)
        return sorted((_claimed(row) for row in rows), key=lambda row: (row.next_attempt_at, row.event.id))

    def _fenced(self, row: ClaimedEvent) -> tuple[ColumnElement[bool], ...]:
        inbox = self._database.tables.inbox
        return (
            inbox.c.consumer == self._consumer.name,
            inbox.c.event_id == row.event.id,
            inbox.c.lease_token == row.lease_token,
        )

    def _span(self, event: RawEvent) -> AbstractContextManager[Span | None]:
        tracing = self._tracing
        if tracing is None:
            return contextlib.nullcontext()
        return tracing.consuming(self._consumer.name, event.traceparent)

    def _failure(self, row: ClaimedEvent, error: Exception) -> _Failure:
        code = error.code if isinstance(error, LedgerError) else "internal"
        invalid = isinstance(error, LedgerError) and error.kind is ErrorKind.INVALID
        return _Failure(code, invalid or row.attempts >= self._consumer.max_attempts, error_type(error))

    async def handle(self, row: ClaimedEvent) -> None:
        """Handle one claimed row (see the module); a handler's failure is recorded.

        An inside handler runs in the row's own unit; an outside one with no unit open, under the row's lease.
        A cancellation is raised, also when a clean-up turned it into another error: the row stays ``running``
        until its lease runs out, and the attempt it used is counted.
        """
        handler = self._consumer.handler
        if isinstance(handler, Outside):
            await self._handle_outside(row, handler.handler)
            return
        name, event = self._consumer.name, row.event
        failure: _Failure | None = None
        try:
            with self._span(event) as span, acting_as(_actor(name, event)):
                async with self._database.unit(event.scope) as unit:
                    _fence((await unit.connection.execute(self._extended(row))).first())
                    failure = await self._attempt(row, partial(self._inside, row, unit, handler))
                    if failure is not None:
                        _failed(span, failure)
                        (await unit.connection.execute(self._recorded(row, failure))).one()  # still ours
        except _LeaseLostError:
            self._lost(row)
            return
        self._count(failure)

    async def _inside(self, row: ClaimedEvent, unit: UnitOfWork, handler: Handler) -> None:
        """The handler, then the row's deletion, in a savepoint of ``unit``: undone together if it fails."""
        async with unit.savepoint():
            await handler(row.event, unit)
            inbox = self._database.tables.inbox
            await unit.connection.execute(delete(inbox).where(*self._fenced(row)))

    async def _handle_outside(self, row: ClaimedEvent, handler: OutsideHandler) -> None:
        """Run ``handler`` with no unit open under the row's lease, then settle the row (see the module)."""
        name, event = self._consumer.name, row.event
        try:
            with self._span(event) as span, acting_as(_actor(name, event)):
                failure = await self._attempt(row, partial(self._outside, row, handler))
                if failure is not None:
                    _failed(span, failure)
                await self._settle(row, failure)
        except _LeaseLostError:
            self._lost(row)
            return
        self._count(failure)

    async def _outside(self, row: ClaimedEvent, handler: OutsideHandler) -> None:
        """``handler``, its lease extended first and renewed every third of its length while it runs.

        ``_LeaseLostError`` when the row was taken over since the claim (the handler never starts), or while
        it ran (it is stopped), or when a renewal failed.
        """
        if not await self._renewed(row):
            raise _LeaseLostError
        await renewing(
            self._bounded(handler, row.event),
            renew=partial(self._renewed, row),
            every=self._lease.total_seconds() / 3,
            lost=_LeaseLostError,
        )

    async def _bounded(self, handler: OutsideHandler, event: RawEvent) -> None:
        """``handler``, or ``InboxHandlerTimedOutError`` when it runs past the consumer's ``timeout``.

        A ``TimeoutError`` the handler raises itself (a call of its own that timed out) is its own failure:
        only the limit set here is told apart.
        """
        limit = asyncio.timeout(self._consumer.timeout.total_seconds())
        try:
            async with limit:
                await handler(event)
        except TimeoutError as error:
            if limit.expired():
                raise InboxHandlerTimedOutError(self._consumer.name) from error
            raise

    async def _attempt(self, row: ClaimedEvent, run: Callable[[], Awaitable[None]]) -> _Failure | None:
        """``run`` the handler; how its failure is recorded if it failed (a lost lease is raised)."""
        try:
            await run()
        except _LeaseLostError:
            raise
        except Exception as error:  # a handler may raise anything; a cancellation is no Exception
            reraise_when_stopping(error)
            failure = self._failure(row, error)
            _logger.exception(
                "inbox.handler_failed",
                consumer=self._consumer.name,
                event_id=str(row.event.id),
                event_type=row.event.type,
                attempts=row.attempts,
                dead=failure.dead,
            )
            return failure
        return None

    def _extended(self, row: ClaimedEvent) -> Update:
        """The row's lease, extended by the runner's length from now while the row's token is ``row``'s."""
        inbox = self._database.tables.inbox
        return (
            update(inbox)
            .where(*self._fenced(row))
            .values(lease_until=func.statement_timestamp() + self._lease)
            .returning(inbox.c.event_id)
        )

    async def _renewed(self, row: ClaimedEvent) -> bool:
        """Whether the row's lease was extended in a unit of its own; a failure counts as a lost lease."""
        try:
            async with self._database.unit(None) as unit:
                return (await unit.connection.execute(self._extended(row))).first() is not None
        except Exception as error:  # the database: the lease cannot be vouched for, another may take the row
            reraise_when_stopping(error)
            _logger.exception(
                "inbox_lease_renewal_failed", consumer=self._consumer.name, event_id=str(row.event.id)
            )
            return False

    async def _settle(self, row: ClaimedEvent, failure: _Failure | None) -> None:
        """Delete the handled row or record its failure, while it is ours (else ``_LeaseLostError``)."""
        inbox = self._database.tables.inbox
        settled = (
            delete(inbox).where(*self._fenced(row)).returning(inbox.c.event_id)
            if failure is None
            else self._recorded(row, failure)
        )
        async with self._database.unit(None) as unit:
            _fence((await unit.connection.execute(settled)).first())

    def _count(self, failure: _Failure | None) -> None:
        outcome = "done" if failure is None else "dead" if failure.dead else "retry"
        self._handled.labels(consumer=self._consumer.name, outcome=outcome).inc()

    def _lost(self, row: ClaimedEvent) -> None:
        _logger.warning("inbox_lease_lost", consumer=self._consumer.name, event_id=str(row.event.id))
        self._handled.labels(consumer=self._consumer.name, outcome="lease_lost").inc()

    def _recorded(self, row: ClaimedEvent, failure: _Failure) -> Update:
        """The failure's record: ``dead`` and kept, or ready after the backoff (the row is still ours)."""
        inbox = self._database.tables.inbox
        outcome = (
            {"status": "dead"}
            if failure.dead
            else {
                "status": "ready",
                "next_attempt_at": func.statement_timestamp() + self._backoff.after(row.attempts),
            }
        )
        return (
            update(inbox)
            .where(*self._fenced(row))
            .values(**outcome, lease_until=None, lease_token=None, last_error_code=failure.code)
            .returning(inbox.c.event_id)
        )

    async def replay(self, event_id: UUID) -> bool:
        """Make the consumer's dead row of ``event_id`` ready now with fresh attempts; whether there was one.

        What an operator's replay runs (§3.5); a row in any other state is left as it is. The revived row
        is announced (``ledger_inbox``) when the unit commits. ``ReplayRefusedError`` for an ordered
        consumer's row while an earlier row of its subject is live (see the module).
        """
        inbox = self._database.tables.inbox
        blocked = (
            select(inbox.c.event_id)
            .where(
                inbox.c.consumer == self._consumer.name,
                inbox.c.event_id == event_id,
                inbox.c.status == "dead",
                _earlier_live(inbox),
            )
            .exists()
        )
        revived = (
            update(inbox)
            .where(
                inbox.c.consumer == self._consumer.name,
                inbox.c.event_id == event_id,
                inbox.c.status == "dead",
            )
            .values(status="ready", attempts=0, next_attempt_at=func.now(), last_error_code=None)
            .returning(inbox.c.event_id)
        )
        async with self._database.unit(None) as unit:
            if self._consumer.ordered and _BLOCKED.validate_python(
                (await unit.connection.execute(select(blocked))).scalar_one()
            ):
                raise ReplayRefusedError(self._consumer.name)
            found = (await unit.connection.execute(revived)).first() is not None
            if found:
                await unit.connection.execute(_NOTIFY)
        return found

    async def measure(self) -> None:
        """Export the age of the oldest ready row of a type the consumer has no handler for (0 if none)."""
        inbox = self._database.tables.inbox
        oldest = select(
            func.coalesce(func.extract("epoch", func.now() - func.min(inbox.c.next_attempt_at)), 0)
        ).where(
            inbox.c.consumer == self._consumer.name,
            inbox.c.status == "ready",
            inbox.c.type.not_in(sorted(self._consumer.types)),
        )
        async with self._database.unit(None) as unit:
            age = _AGE.validate_python((await unit.connection.execute(oldest)).scalar_one())
        self._waiting.labels(consumer=self._consumer.name).set(age)

    def stop(self) -> None:
        """Claim nothing more: finish the row in hand, give the rest back, then set ``drained``."""
        self._stopping.set()
        if self._woken is not None:
            self._woken.set()

    async def run_once(self) -> int:
        """Claim the due rows and handle each, in order, until asked to stop; how many were claimed."""
        rows = await self.claim_now()
        pending = list(rows)
        started = False
        try:
            while pending and not self._stopping.is_set():
                started = True
                await self.handle(pending[0])
                started = False
                del pending[0]
        finally:
            if pending:
                await self._give_back(pending, started=started)
        return len(rows)

    async def _give_back(self, rows: Sequence[ClaimedEvent], *, started: bool) -> None:
        """Release ``rows`` (the first one's handler ``started``) in a short shielded unit of its own."""
        held: list[tuple[object, ...]] = [
            (self._consumer.name, row.event.id, row.lease_token) for row in rows
        ]
        split = int(started)
        async with asyncio.timeout(RELEASE_TIMEOUT):
            await asyncio.shield(self._released(held[:split], held[split:]))
        _logger.info("inbox_released", consumer=self._consumer.name, rows=len(held))

    async def _released(
        self, started: Sequence[tuple[object, ...]], unstarted: Sequence[tuple[object, ...]]
    ) -> None:
        inbox = self._database.tables.inbox
        async with self._database.unit(None) as unit:
            keys = ("consumer", "event_id")
            await release(unit.connection, inbox, keys=keys, started=started, unstarted=unstarted)

    async def run_until_idle(self) -> int:
        """Run until a claim finds nothing (tests drain with it: absence is proved by draining, §14.2)."""
        total = 0
        while not self._stopping.is_set() and (handled := await self.run_once()):
            total += handled
        return total

    async def run(self, wakeups: Waker) -> Never:
        """Until stopped: drain, then wait for a NOTIFY on ``ledger_inbox`` or the poll (the supervised loop).

        Stopped, it sets ``drained`` and waits to be cancelled.
        """
        woken = self._woken = wakeups.subscribe(INBOX)
        while not self._stopping.is_set():
            await self.run_until_idle()
            await self.measure()
            await wakeups.wait(woken, poll=POLL)
        self.drained.set()
        await parked()
