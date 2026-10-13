"""The inbox runner (§3.5): one consumer's rows, claimed with a lease and handled exactly once in effect.

A claim, in a short unit of its own, first recovers the consumer's rows whose lease ran out (``ready`` a
backoff later, or ``dead`` with ``worker_lost`` once the attempts are spent, ``inbox_worker_lost``), then
takes due ``ready`` rows of the consumer's own types, counts the attempt, and sets a fencing token. Each row
is handled in its own unit, in the consumer's module role and the event's scope. The unit's first statement
extends the lease, fenced by the token (``WHERE lease_token = :token``): if it finds nothing, another worker
took the row over and the handler does not run (``inbox_lease_lost``); if it does, the unit holds the row's
lock until it ends, so no other worker can claim the row while the handler works, however long that takes. The
handler's writes and the deletion of the row commit together. A failure is recorded in another unit, fenced
the same way: retried with backoff, or ``dead`` (kept, replayable) when the error is ``INVALID`` or the
attempts are spent.

An ordered consumer claims only the first row of each subject that is not dead, in the order
(``aggregate_version``, event id), a missing version first. Consequences (Decision 18): a dead row no longer
holds back its subject, so the rows behind it run before it, and so would a replay of it; a replay is
therefore refused while an earlier row of its subject is still live. Events without a subject are not ordered
(they have no subject to queue behind).

Every time is the database's (§3.6): due rows and expired leases are compared with the claim unit's ``now()``;
a lease extension and a retry run from the statement that writes them (``statement_timestamp()``).
``ledger_inbox_unhandled_oldest_seconds`` is the age of the consumer's oldest ready row of a type it has no
handler for (rows of a newer version's types wait for its rollout, §3.5), measured once per drain.
"""

import contextlib
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, Never, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import Generator, Sequence
lazy from datetime import datetime
lazy from uuid import UUID

import structlog
from pydantic import TypeAdapter
from sqlalchemy import and_, delete, exists, func, select, text, tuple_, update
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import ColumnElement, RowMapping

from ledger_kernel.errors import ConflictError, ErrorKind, LedgerError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.notifier import INBOX
from ledger_platform.data.queue import DEFAULT_BACKOFF, claim, recover
from ledger_platform.events.consumers import raw_event_of
from ledger_platform.events.dispatcher import POLL, reraise_when_stopping
from ledger_platform.events.metrics import counter, gauge
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.data.queue import Backoff
lazy from ledger_platform.events.consumers import Consumer, RawEvent
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

    The order is (``aggregate_version``, event id), a missing version first; a row without a subject has none
    before it (``NULL = NULL`` is not true).
    """
    older = inbox.alias("older")
    return exists().where(
        older.c.consumer == inbox.c.consumer,
        older.c.subject == inbox.c.subject,
        tuple_(func.coalesce(older.c.aggregate_version, -1), older.c.event_id)
        < tuple_(func.coalesce(inbox.c.aggregate_version, -1), inbox.c.event_id),
        older.c.status != "dead",
    )


def _fence(deleted: object) -> None:
    """``_LeaseLostError`` unless the fenced deletion returned the row (it is still this runner's)."""
    if deleted is None:
        raise _LeaseLostError


@final
class InboxRunner:
    """See the module."""

    __slots__ = ("_backoff", "_batch", "_consumer", "_database", "_handled", "_lease", "_tracing", "_waiting")

    def __init__(
        self, *, database: BoundDatabase, consumer: Consumer, **options: Unpack[RunnerOptions]
    ) -> None:
        self._database = database
        self._consumer = consumer
        self._lease = options.get("lease", DEFAULT_LEASE)
        self._batch = options.get("batch", DEFAULT_BATCH)
        self._backoff = options.get("backoff", DEFAULT_BACKOFF)
        self._tracing = options.get("tracing")
        registry = options.get("registry")
        self._handled = counter(
            registry,
            "ledger_inbox_handled",
            "Inbox rows handled, by consumer and outcome.",
            ["consumer", "outcome"],
        )
        for outcome in OUTCOMES:
            self._handled.labels(consumer=consumer.name, outcome=outcome)  # exported at zero
        self._waiting = gauge(
            registry,
            "ledger_inbox_unhandled_oldest_seconds",
            "Age of the oldest ready row of a type the consumer has no handler for.",
            ["consumer"],
        )

    def _due(self) -> ColumnElement[bool]:
        inbox = self._database.tables.inbox
        now = func.now()
        due = and_(
            inbox.c.consumer == self._consumer.name,
            inbox.c.type.in_(sorted(self._consumer.types)),
            inbox.c.status == "ready",
            inbox.c.next_attempt_at <= now,
        )
        return and_(due, ~_earlier_live(inbox)) if self._consumer.ordered else due

    async def claim_now(self) -> Sequence[ClaimedEvent]:
        """Claim the due rows (see the module), oldest first; ``run_once`` handles them (a test seam too)."""
        inbox = self._database.tables.inbox
        keys = ("consumer", "event_id")
        async with self._database.unit(None) as unit:
            lost = await recover(
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
                due=self._due(),
                order_by=(inbox.c.next_attempt_at,),
                limit=self._batch,
                lease=self._lease,
            )
        if lost:
            _logger.warning("inbox_worker_lost", consumer=self._consumer.name, rows=lost)
            self._handled.labels(consumer=self._consumer.name, outcome="dead").inc(lost)
        return sorted((_claimed(row) for row in rows), key=lambda row: (row.next_attempt_at, row.event.id))

    def _fenced(self, row: ClaimedEvent) -> tuple[ColumnElement[bool], ...]:
        inbox = self._database.tables.inbox
        return (
            inbox.c.consumer == self._consumer.name,
            inbox.c.event_id == row.event.id,
            inbox.c.lease_token == row.lease_token,
        )

    @contextlib.contextmanager
    def _span(self, event: RawEvent) -> Generator[None]:
        if self._tracing is None:
            yield
            return
        with self._tracing.consuming(self._consumer.name, event.traceparent):
            yield

    async def handle(self, row: ClaimedEvent) -> None:
        """Handle one claimed row in its own unit (see the module); a handler's failure is recorded.

        A cancellation is raised, also when a clean-up turned it into another error: the row stays ``running``
        until its lease runs out, and the attempt it used is counted.
        """
        name, event = self._consumer.name, row.event
        inbox = self._database.tables.inbox
        try:
            # The error leaves the span before it is handled here: a failed handling shows in the trace.
            with self._span(event), acting_as(Actor(f"system:consumer:{name}", str(event.id))):
                async with self._database.unit(event.scope) as unit:
                    extended = (
                        update(inbox)
                        .where(*self._fenced(row))
                        .values(lease_until=func.statement_timestamp() + self._lease)
                        .returning(inbox.c.event_id)
                    )
                    _fence((await unit.connection.execute(extended)).first())
                    await self._consumer.handler(event, unit)
                    deleted = delete(inbox).where(*self._fenced(row)).returning(inbox.c.event_id)
                    _fence((await unit.connection.execute(deleted)).first())
        except _LeaseLostError:
            self._lost(row)
        except Exception as error:  # a handler may raise anything; a cancellation is no Exception
            reraise_when_stopping(error)
            dead = (isinstance(error, LedgerError) and error.kind is ErrorKind.INVALID) or (
                row.attempts >= self._consumer.max_attempts
            )
            _logger.exception(
                "inbox.handler_failed",
                consumer=name,
                event_id=str(event.id),
                event_type=event.type,
                attempts=row.attempts,
                dead=dead,
            )
            await self._failed(row, error.code if isinstance(error, LedgerError) else "internal", dead=dead)
        else:
            self._handled.labels(consumer=name, outcome="done").inc()

    def _lost(self, row: ClaimedEvent) -> None:
        _logger.warning("inbox_lease_lost", consumer=self._consumer.name, event_id=str(row.event.id))
        self._handled.labels(consumer=self._consumer.name, outcome="lease_lost").inc()

    async def _failed(self, row: ClaimedEvent, code: str, *, dead: bool) -> None:
        """Record the failure, fenced: ``dead`` and kept, or ready again after the backoff."""
        inbox = self._database.tables.inbox
        outcome = (
            {"status": "dead"}
            if dead
            else {
                "status": "ready",
                "next_attempt_at": func.statement_timestamp() + self._backoff.after(row.attempts),
            }
        )
        recorded = (
            update(inbox)
            .where(*self._fenced(row))
            .values(**outcome, lease_until=None, lease_token=None, last_error_code=code)
            .returning(inbox.c.event_id)
        )
        async with self._database.unit(None) as unit:
            found = (await unit.connection.execute(recorded)).first()
        if found is None:
            self._lost(row)
            return
        self._handled.labels(consumer=self._consumer.name, outcome="dead" if dead else "retry").inc()

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

    async def run_once(self) -> int:
        """Claim the due rows and handle each, in order; how many were claimed."""
        rows = await self.claim_now()
        for row in rows:
            await self.handle(row)
        return len(rows)

    async def run_until_idle(self) -> int:
        """Run until a claim finds nothing (tests drain with it: absence is proved by draining, §14.2)."""
        total = 0
        while handled := await self.run_once():
            total += handled
        return total

    async def run(self, wakeups: Waker) -> Never:
        """Forever: drain, then wait for a NOTIFY on ``ledger_inbox`` or the poll (the supervised loop)."""
        woken = wakeups.subscribe(INBOX)
        while True:
            await self.run_until_idle()
            await self.measure()
            await wakeups.wait(woken, poll=POLL)
