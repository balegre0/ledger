"""The inbox runner (§3.5): one consumer's rows, claimed with a lease and handled exactly once in effect.

A claim, in a short unit of its own, takes due rows of the consumer's own types (``ready`` and due, or
``running`` with an expired lease), counts the attempt, and sets a fencing token. Each row is handled in its
own unit, in the consumer's module role and the event's scope: the handler's writes and the deletion of the
row (``WHERE lease_token = :token``) commit together; if the deletion finds nothing, another worker took the
row over and this unit rolls back (``inbox_lease_lost``). A failure is recorded in another unit, fenced the
same way: retried with backoff, or ``dead`` (kept, replayable) when the error is ``INVALID`` or the attempts
are spent. An ordered consumer claims only the oldest row of each subject that is not dead (Decision 18).

Every time is the database's (§3.6): due rows and expired leases are compared with the claim unit's
``now()``, a retry is due ``backoff`` after the statement that records it (``statement_timestamp()``).
"""

import contextlib
import weakref
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, Never, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import Generator, Sequence
lazy from datetime import datetime
lazy from uuid import UUID

import structlog
from prometheus_client import Counter
from pydantic import TypeAdapter
from sqlalchemy import and_, delete, exists, func, or_, update
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import ColumnElement, RowMapping

from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.notifier import INBOX
from ledger_platform.data.queue import DEFAULT_BACKOFF, claim
from ledger_platform.events.consumers import raw_event_of
from ledger_platform.events.dispatcher import POLL
from ledger_platform.runtime.supervision import being_cancelled
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.queue import Backoff
lazy from ledger_platform.events.consumers import Consumer, RawEvent
lazy from ledger_platform.events.wakeups import Waker
lazy from ledger_platform.observability.tracing import Tracing

OUTCOMES: Final = ("done", "retry", "dead", "lease_lost")
"""The values of ``ledger_inbox_handled_total``'s ``outcome`` label."""
DEFAULT_LEASE: Final = timedelta(seconds=60)
DEFAULT_BATCH: Final = 20
_COUNTERS: Final[weakref.WeakKeyDictionary[CollectorRegistry, Counter]] = weakref.WeakKeyDictionary()
_ROW: Final = TypeAdapter(dict[str, object])
_logger = structlog.stdlib.get_logger(__name__)


class _LeaseLostError(LedgerError):
    """The row was taken over while its handler ran: the unit must roll back (caught by the runner)."""

    code: ClassVar[str] = "inbox_lease_lost"

    def __init__(self) -> None:
        super().__init__("the inbox row's lease was taken over by another worker")


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


def _fence(deleted: object) -> None:
    """``_LeaseLostError`` unless the fenced deletion returned the row (it is still this runner's)."""
    if deleted is None:
        raise _LeaseLostError


def _handled_counter(registry: CollectorRegistry | None) -> Counter:
    """``ledger_inbox_handled_total{consumer, outcome}``: one per registry, shared by its runners."""
    found = None if registry is None else _COUNTERS.get(registry)
    if found is not None:
        return found
    counter = Counter(
        "ledger_inbox_handled",
        "Inbox rows handled, by consumer and outcome.",
        ["consumer", "outcome"],
        registry=registry,
    )
    if registry is not None:
        _COUNTERS[registry] = counter
    return counter


@final
class InboxRunner:
    """See the module."""

    __slots__ = ("_backoff", "_batch", "_consumer", "_database", "_handled", "_lease", "_tracing")

    def __init__(
        self, *, database: BoundDatabase, consumer: Consumer, **options: Unpack[RunnerOptions]
    ) -> None:
        self._database = database
        self._consumer = consumer
        self._lease = options.get("lease", DEFAULT_LEASE)
        self._batch = options.get("batch", DEFAULT_BATCH)
        self._backoff = options.get("backoff", DEFAULT_BACKOFF)
        self._tracing = options.get("tracing")
        self._handled = _handled_counter(options.get("registry"))
        for outcome in OUTCOMES:
            self._handled.labels(consumer=consumer.name, outcome=outcome)  # exported at zero

    def _due(self) -> ColumnElement[bool]:
        inbox = self._database.tables.inbox
        now = func.now()
        due = and_(
            inbox.c.consumer == self._consumer.name,
            inbox.c.type.in_(sorted(self._consumer.types)),
            or_(
                and_(inbox.c.status == "ready", inbox.c.next_attempt_at <= now),
                and_(inbox.c.status == "running", inbox.c.lease_until < now),
            ),
        )
        if not self._consumer.ordered:
            return due
        older = inbox.alias("older")
        return and_(
            due,
            ~exists().where(
                older.c.consumer == inbox.c.consumer,
                older.c.subject == inbox.c.subject,
                older.c.event_id < inbox.c.event_id,
                older.c.status != "dead",
            ),
        )

    async def claim_now(self) -> Sequence[ClaimedEvent]:
        """Claim the due rows (see the module), oldest first; ``run_once`` handles them (a test seam too)."""
        inbox = self._database.tables.inbox
        async with self._database.unit(None) as unit:
            rows = await claim(
                unit.connection,
                inbox,
                keys=("consumer", "event_id"),
                due=self._due(),
                order_by=(inbox.c.next_attempt_at, inbox.c.event_id),
                limit=self._batch,
                lease=self._lease,
            )
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
                    await self._consumer.handler(event, unit)
                    deleted = delete(inbox).where(*self._fenced(row)).returning(inbox.c.event_id)
                    _fence((await unit.connection.execute(deleted)).first())
        except _LeaseLostError:
            self._lost(row)
        except Exception as error:  # a handler may raise anything; a cancellation is no Exception
            if being_cancelled():  # a stop that a clean-up (the rollback) turned into another error
                raise
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

        What an operator's replay runs (§3.5); a row in any other state is left as it is.
        """
        inbox = self._database.tables.inbox
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
            return (await unit.connection.execute(revived)).first() is not None

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
            await wakeups.wait(woken, poll=POLL)
