"""Transactional outbox: an event commits with the state change it announces, then a relay publishes it.

The relay publishes a batch and waits for JetStream's acknowledgements inside the transaction that locks
the rows (``FOR UPDATE SKIP LOCKED``), then deletes the rows JetStream acknowledged and commits: an event
leaves the table only after JetStream has stored it. One that was not acknowledged stays, on a schedule of
its own (``attempts`` and ``next_attempt_at``: the relay's ``retry``, doubling up to ``MAX_RETRY_DELAY``),
and a pass takes only the rows that are due, the ones never tried before the ones that failed. However many
events are stuck, they do not delay a fresh one. One published twice (a crash between the acknowledgement
and the commit, or a retry) is dropped by JetStream's de-duplication (``Nats-Msg-Id``) within the stream's
window, and by the consumer's inbox after that.

A commit wakes the relay of the same process at once; rows written by other replicas, and events that
are due again, are picked up within ``idle`` seconds by any relay. Fresh events go out oldest first by event
time, then the retried ones in the same order, but order is only kept within a pass and a relay: an event
that failed follows the ones published after it, and replicas take batches in turn. Consumers rely on the
version of the aggregate an event carries, never on the order of unrelated events.

Events nobody could ever publish (a subject NATS cannot route, a message over its size limit) are
refused when they are added, in the caller's transaction. What is left to wait for is a stream that does not
exist yet. One of the service's own that someone deleted while it ran is declared again by the relay, at most
once per ``STREAMS_ENSURED_EVERY`` seconds.
"""

import asyncio
import contextlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil, inf, log2
from typing import ClassVar, Final, Never, final
from uuid import UUID
lazy from collections.abc import Iterator, Mapping, Sequence

import structlog
from nats.errors import Error as NatsError
from nats.js.errors import NoStreamResponseError
from prometheus_client import Counter, Gauge
from sqlalchemy import (
    DateTime,
    Index,
    Interval,
    LargeBinary,
    delete,
    func,
    insert,
    literal_column,
    select,
    text,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
lazy from nats.js import JetStreamContext
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import Update

from ledger_kernel.errors import ErrorKind, LedgerError, error_type, excerpt
from ledger_platform.database import PlatformTable
from ledger_platform.options import InvalidOptionError, require_at_least, require_seconds
from ledger_platform.streams import StreamRefusedError, ensure_stream
from ledger_platform.supervision import being_cancelled
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_kernel.clock import Sleep
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.streams import StreamSpec
lazy from ledger_platform.tracing import Tracing

MAX_BATCH: Final = 10_000
"""The most events one pass takes: each is a bind parameter of its ``DELETE`` (PostgreSQL allows 65,535) and a
publish in flight at once."""
DEFAULT_PUBLISH_TIMEOUT: Final = 5.0
"""Seconds a pass waits for the acknowledgements of its batch."""
MAX_PUBLISH_TIMEOUT: Final = 20.0
"""The longest ``publish_timeout`` allowed. The rows stay locked and the session idle in its transaction while
a pass waits, and PostgreSQL ends a session idle for ``idle_in_transaction_session_timeout`` (60 seconds by
default): this leaves it room for the rest of the pass."""
DEFAULT_RETRY: Final = 1.0
"""Seconds before the first retry, of a stuck event and of a failed pass; each next one is twice as long."""
MIN_RETRY_DELAY: Final = 0.001
"""Retrying sooner than this is a busy loop."""
MAX_RETRY_DELAY: Final = 30.0
"""The longest delay from a failure to the next attempt, for an event and for a failed pass alike."""
MAX_IDLE: Final = 30.0
"""The longest ``idle`` allowed: how long a row of another replica, or an event that is due again, can wait
for the poll that finds it. With the two ceilings above, an event is tried again about 80 seconds after the
attempt before (20 + 30 + 30), and up to 100 when a slow pass is in flight when it falls due (20 more): still
inside the two-minute duplicate window of a stream by default."""
DEFAULT_MAX_PAYLOAD: Final = 1024 * 1024
"""NATS's default ``max_payload``, which counts the headers of a message as well as its body. A service passes
the one its connection reports."""
STREAMS_ENSURED_EVERY: Final = 30.0
"""The shortest time between two attempts of a relay to declare its service's streams again, when events find
no stream: often enough to heal a stream deleted by mistake within a minute, seldom enough not to hammer a
JetStream that keeps refusing."""
MAX_SUBJECT_LENGTH: Final = 255
"""Far below NATS's limit (a control line of 4,096 bytes): a longer subject is a mistake."""
_LABEL: Final = r"(?:(?![.*>])[!-~])+"
_SUBJECT: Final = re.compile(rf"{_LABEL}(?:\.{_LABEL})*", re.ASCII)
"""Labels of visible ASCII (no space, no ``.``, no wildcard ``*`` or ``>``) separated by single dots."""
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_logger = structlog.stdlib.get_logger(__name__)


class InvalidOutboxOptionError(InvalidOptionError):
    """An outbox option that cannot work."""

    code: ClassVar[str] = "invalid_outbox_option"
    label: ClassVar[str] = "outbox option"


class UnpublishableEventError(LedgerError):
    """An event the outbox refuses to store because no relay could ever publish it.

    A bug in the code that adds it: the caller's transaction is the place to find out, not a relay that would
    retry for ever.
    """


class InvalidSubjectError(UnpublishableEventError):
    """The subject cannot be published to: NATS would never route the event."""

    code: ClassVar[str] = "invalid_event_subject"

    def __init__(self, subject: str) -> None:
        rule = (
            f"visible ASCII labels separated by dots, no wildcards, at most {MAX_SUBJECT_LENGTH} characters"
        )
        super().__init__(f"{excerpt(subject)} is not a subject an event can be published to: {rule}")


class EventTooLargeError(UnpublishableEventError):
    """The event is larger than the message NATS accepts: the server would refuse it, and so for ever."""

    code: ClassVar[str] = "event_too_large"

    def __init__(self, *, event_id: UUID, size: int, limit: int) -> None:
        super().__init__(
            f"event {event_id} takes {size} bytes on the wire, headers included; NATS accepts at most {limit}"
        )


type Failure = tuple[UUID, str, BaseException]
"""An event JetStream did not acknowledge: its id, its subject and why."""


class PublishFailedError(LedgerError):
    """JetStream did not acknowledge events of a pass: they stay in the outbox for the next one.

    Raised after the pass has removed the events JetStream did acknowledge. Built from the ones it did not, in
    the order of the pass; names the first, and never what an event says. The cause is the client's own error
    for that first one.
    """

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "outbox_publish_failed"

    event_id: UUID
    subject: str
    error_type: str
    """The qualified class name of what went wrong with that event: its message is for the traceback."""
    acknowledged: int
    """How many events of the pass JetStream did acknowledge (and the pass removed)."""
    failed: int
    """How many events of the pass it did not."""
    missing_stream: bool
    """Whether JetStream had no stream for one of them: its subject belongs to no stream (yet)."""

    def __init__(self, failures: Sequence[Failure], *, acknowledged: int) -> None:
        (event_id, subject, reason), *_ = failures
        what = f"event {event_id} was not acknowledged by JetStream on subject {excerpt(subject)}"
        super().__init__(f"{what}: {len(failures)} events of the pass failed and {acknowledged} went out")
        self.event_id = event_id
        self.subject = subject
        self.error_type = error_type(reason)
        self.acknowledged = acknowledged
        self.failed = len(failures)
        self.missing_stream = any(isinstance(cause, NoStreamResponseError) for _, _, cause in failures)


def _require_retry(retry: float) -> None:
    require_seconds(InvalidOutboxOptionError, "retry", retry, floor=MIN_RETRY_DELAY, ceiling=MAX_RETRY_DELAY)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RelayPace:
    """How often a relay looks for events, and how soon it tries again after a failure."""

    idle: float = 1.0
    """Seconds a relay waits for a commit of its process before it looks again: also how soon it finds an
    event that is due again or that another replica wrote. At most ``MAX_IDLE``."""
    retry: float = DEFAULT_RETRY
    """Seconds before the first retry, of an event that failed and of a pass that failed; each next one is
    twice as long, up to ``MAX_RETRY_DELAY``. At least ``MIN_RETRY_DELAY``."""

    def __post_init__(self) -> None:
        require_seconds(InvalidOutboxOptionError, "idle", self.idle, ceiling=MAX_IDLE)
        _require_retry(self.retry)


DEFAULT_PACE: Final = RelayPace()


def _require_subject(subject: str) -> None:
    if len(subject) > MAX_SUBJECT_LENGTH or _SUBJECT.fullmatch(subject) is None:
        raise InvalidSubjectError(subject)


def _wire_size(headers: Mapping[str, str], data: bytes) -> int:
    """The bytes the server counts against ``max_payload``: nats-py's header block, and the body.

    The header block is the version line, a ``name: value`` line for each header and an empty line. Names and
    values are visible ASCII (``Envelope`` guarantees it), so a character is a byte.
    """
    lines = sum(len(name) + len(": ") + len(value) + len("\r\n") for name, value in headers.items())
    return len("NATS/1.0\r\n") + lines + len("\r\n") + len(data)


async def _declare_again(jetstream: JetStreamContext, streams: Sequence[StreamSpec]) -> None:
    """Ensure ``streams`` once more, for events that found none; a failure is logged and goes no further."""
    names = [spec.name for spec in streams]
    try:
        for spec in streams:
            await ensure_stream(jetstream, spec)
    except StreamRefusedError, NatsError:
        _logger.exception("outbox.streams_not_ensured", streams=names)
    else:
        _logger.warning("outbox.streams_ensured", streams=names)


def _delays(first: float) -> Iterator[float]:
    """``first``, then twice the one before, never more than ``MAX_RETRY_DELAY``."""
    delay = first
    while True:
        yield delay
        delay = min(delay * 2, MAX_RETRY_DELAY)


class OutboxRecord(PlatformTable):
    """An event committed but not yet acknowledged by JetStream."""

    __tablename__: str = "outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column()
    headers: Mapped[dict[str, str]] = mapped_column(JSONB)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """The event's own time, which orders the relay's reads: the sender's clock, so skewed at worst."""
    stored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """When the event entered the outbox, by the database's clock: what the backlog is dated from."""
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    """How many times publishing the event failed."""
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """When the relay may try again: at once for a new event, later for one that failed."""


_RETRIED: Final = OutboxRecord.attempts > literal_column("0")
"""Whether the event failed before. The zero is in the SQL, not bound, so that the index matches it."""
Index("ix_outbox_due", _RETRIED, OutboxRecord.created_at, OutboxRecord.id, OutboxRecord.next_attempt_at)
"""The relay reads the due rows in ``(retried, created_at, id)`` order: the events never tried first. With
``next_attempt_at`` in the index PostgreSQL skips the rows that are not due without visiting them, and a
pass starts where the fresh events are: hundreds of thousands of stuck rows cost a pass a few milliseconds."""
_BACKLOG: Final = select(
    func.count(),
    type_coerce(
        func.coalesce(func.min(OutboxRecord.stored_at), type_coerce(_EPOCH, DateTime(timezone=True))),
        DateTime(timezone=True),
    ),
)
"""How many events wait, and when the oldest was stored (the epoch when none is)."""


def _rescheduled(ids: Sequence[UUID], retry: float) -> Update:
    """Count a failed attempt for ``ids`` and date the next one.

    The next attempt is ``retry`` seconds after the first failure, twice as many after the second, and so on
    up to ``MAX_RETRY_DELAY``. The exponent stops where the delay reaches the ceiling, so that a row with
    thousands of attempts does not overflow ``power``.
    """
    doublings = max(0, ceil(log2(MAX_RETRY_DELAY / retry)))
    delay = func.least(retry * func.power(2, func.least(OutboxRecord.attempts, doublings)), MAX_RETRY_DELAY)
    due = func.clock_timestamp() + type_coerce(func.make_interval(0, 0, 0, 0, 0, 0, delay), Interval)
    return (
        update(OutboxRecord)
        .where(OutboxRecord.id.in_(ids))
        .values(attempts=OutboxRecord.attempts + 1, next_attempt_at=due)
    )


@final
@dataclass(frozen=True, slots=True)
class _Pending:
    """An event the relay holds the lock of: what JetStream is given, and the id to delete it by."""

    id: UUID
    subject: str
    headers: dict[str, str]
    data: bytes


@final
class Outbox:
    """Writes events inside units of work and relays them to JetStream.

    At most ``batch`` events go out in one pass, whose publishes wait ``publish_timeout`` seconds for their
    acknowledgements; ``max_payload`` is the size NATS accepts for a message (headers and body).

    Metrics: counters of the events published, of the publishes JetStream did not acknowledge, and of the
    passes that failed as a whole (the database, not a publish); gauges of the number of events waiting and of
    the time the oldest was stored (a timestamp, so ``time() - x`` keeps growing while the relay is blind).
    The gauges are read from the table, so every replica exports the same table-wide values: aggregate them
    with ``max``, never ``sum``. To alert on lag: ``pending > 0 and time() - oldest_pending_timestamp_seconds
    > threshold``; the timestamp is 0 when nothing waits, and ``time() - 0`` alone would read as a lag of
    decades.
    """

    __slots__ = (
        "_batch",
        "_failed_passes",
        "_max_payload",
        "_oldest_pending",
        "_pending",
        "_publish_failed",
        "_publish_timeout",
        "_published",
        "_tracing",
        "_wake",
    )

    def __init__(
        self,
        *,
        tracing: Tracing,
        registry: CollectorRegistry,
        batch: int = 100,
        publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT,
        max_payload: int = DEFAULT_MAX_PAYLOAD,
    ) -> None:
        if not 1 <= batch <= MAX_BATCH:
            raise InvalidOutboxOptionError(
                option="batch", requirement=f"between 1 and {MAX_BATCH}", value=batch
            )
        require_seconds(
            InvalidOutboxOptionError, "publish_timeout", publish_timeout, ceiling=MAX_PUBLISH_TIMEOUT
        )
        require_at_least(InvalidOutboxOptionError, "max_payload", max_payload)
        self._tracing = tracing
        self._batch = batch
        self._publish_timeout = publish_timeout
        self._max_payload = max_payload
        self._wake = asyncio.Event()
        self._published = Counter(
            "ledger_outbox_published", "Events the relay published to JetStream.", registry=registry
        )
        self._publish_failed = Counter(
            "ledger_outbox_publish_failed", "Publishes JetStream did not acknowledge.", registry=registry
        )
        self._pending = Gauge(
            "ledger_outbox_pending",
            "Events committed and not yet acknowledged by JetStream.",
            registry=registry,
        )
        self._oldest_pending = Gauge(
            "ledger_outbox_oldest_pending_timestamp_seconds",
            "Unix time at which the oldest pending event was stored; 0 when there is none.",
            registry=registry,
        )
        self._failed_passes = Counter(
            "ledger_outbox_failed_passes",
            "Relay passes that failed as a whole: the database, not a publish.",
            registry=registry,
        )

    async def add(self, unit: UnitOfWork, envelope: Envelope, *, subject: str) -> None:
        """Store ``envelope`` for ``subject`` in ``unit``'s transaction; the relay wakes when it commits.

        An envelope that carries no trace joins the active one, if there is one. An event no relay could ever
        publish is refused here, so that its transaction fails instead of a relay stalling on it:
        ``InvalidSubjectError``, ``EventTooLargeError``.
        """
        _require_subject(subject)
        traced = (
            envelope
            if envelope.traceparent is not None
            else replace(envelope, traceparent=self._tracing.current_traceparent())
        )
        headers = traced.headers()
        size = _wire_size(headers, traced.data)
        if size > self._max_payload:
            raise EventTooLargeError(event_id=traced.id, size=size, limit=self._max_payload)
        await unit.connection.execute(
            insert(OutboxRecord).values(
                id=traced.id, subject=subject, headers=headers, data=traced.data, created_at=traced.time
            )
        )
        unit.after_commit(self._wake.set)

    async def relay_once(
        self, database: Database, jetstream: JetStreamContext, *, retry: float = DEFAULT_RETRY
    ) -> int:
        """Publish up to one batch of the events that are due, and remove those acknowledged.

        The events never tried come first, oldest first by event time, then the retried ones in the same
        order: a pile of stuck events cannot delay a fresh one.

        The batch is published at once and all acknowledgements awaited together, so the transaction (and the
        row locks in it) lasts one ``publish_timeout`` however many events there are. Every acknowledged event
        is removed, whatever happened to the others. One that failed stays, with its next attempt dated
        ``retry`` seconds ahead (twice as many after the second failure, up to ``MAX_RETRY_DELAY``), and the
        first of them in the batch's order is then raised as ``PublishFailedError``. How many were published.
        """
        _require_retry(retry)
        async with database.transaction(None) as unit:
            events = await self._lock_the_due(unit)
            reasons = await self._publish(jetstream, events)
            acknowledged = [event for event, reason in zip(events, reasons, strict=True) if reason is None]
            failures = [
                (event, reason) for event, reason in zip(events, reasons, strict=True) if reason is not None
            ]
            if acknowledged:
                await unit.connection.execute(
                    delete(OutboxRecord).where(OutboxRecord.id.in_([event.id for event in acknowledged]))
                )
            if failures:
                await unit.connection.execute(_rescheduled([event.id for event, _ in failures], retry))
        self._published.inc(len(acknowledged))
        self._publish_failed.inc(len(failures))
        if failures:
            raise PublishFailedError(
                [(event.id, event.subject, reason) for event, reason in failures],
                acknowledged=len(acknowledged),
            ) from failures[0][1]
        return len(acknowledged)

    async def run(
        self,
        database: Database,
        jetstream: JetStreamContext,
        *,
        streams: Sequence[StreamSpec] = (),
        pace: RelayPace = DEFAULT_PACE,
        sleep: Sleep = asyncio.sleep,
    ) -> Never:
        """Relay forever, one pass after another, at ``pace``.

        A pass that took a whole batch is followed by another at once; after any other, the relay waits for a
        commit of this process, or ``idle`` seconds, which is also how soon after it is due an event that
        failed is tried again, and a row another replica wrote is found.

        Events that failed keep to their own schedule (see ``relay_once``) and are logged once per pass, as
        a warning without a traceback: how many failed and went out, and the first failure's event, subject
        and error type. When one found no stream, the relay declares ``streams`` (the service's own) again
        with ``ensure_stream``, at most once per ``STREAMS_ENSURED_EVERY`` seconds, and logs it: as
        ``outbox.streams_ensured``, or ``outbox.streams_not_ensured`` with the traceback when JetStream would
        not; the events wait either way. A pass that failed as a whole, which means the database, is logged
        with its traceback and counted, and tried again after ``retry`` seconds, then twice that, up to
        ``MAX_RETRY_DELAY``; a pass that works starts the delays over. The backlog gauges are refreshed after
        a pass, full or not, at most once per ``idle`` seconds. ``sleep`` waits between the attempts after a
        failed pass.

        Stops only when cancelled, even when a clean-up turns the cancellation into another error: that error
        ends the relay.
        """
        idle, retry = pace.idle, pace.retry
        delays = _delays(retry)
        loop = asyncio.get_running_loop()
        measured_at = ensured_at = -inf
        while True:
            try:
                attempted = await self.relay_once(database, jetstream, retry=retry)
            except PublishFailedError as failure:
                _logger.warning(
                    "outbox.publish_failed",
                    event_id=str(failure.event_id),
                    subject=failure.subject,
                    error=failure.error_type,
                    acknowledged=failure.acknowledged,
                    failed=failure.failed,
                )
                attempted = failure.acknowledged + failure.failed
                if failure.missing_stream and streams and loop.time() - ensured_at >= STREAMS_ENSURED_EVERY:
                    ensured_at = loop.time()
                    await _declare_again(jetstream, streams)
            except Exception:
                if being_cancelled():  # a stop that a clean-up turned into another error
                    raise
                self._failed_passes.inc()
                delay = next(delays)
                _logger.exception("outbox.relay_failed", retry_in=delay)
                await sleep(delay)
                continue
            delays = _delays(retry)
            if loop.time() - measured_at >= idle:
                await self.measure_backlog(database)
                measured_at = loop.time()
            if attempted < self._batch:
                await self._wait_for_a_commit(idle)

    async def measure_backlog(self, database: Database) -> None:
        """Set the gauges from the table: how many events wait, and when the oldest was stored.

        A transaction of its own, apart from the delivery's, so that reading the backlog can never undo or
        delay what a pass did. A failure is logged and goes no further: the gauges keep their last values.

        To alert on lag: ``pending > 0 and time() - oldest_pending_timestamp_seconds > threshold``. The
        timestamp is 0 when nothing was waiting at the last measure, so it says nothing then; the counter of
        failed passes, and a ``time()`` that moves on past the last measure, are what show a relay gone blind.
        """
        try:
            async with database.transaction(None) as unit:
                waiting, oldest = (await unit.connection.execute(_BACKLOG)).one()
        except Exception:
            if being_cancelled():  # a stop that a clean-up turned into another error
                raise
            _logger.exception("outbox.backlog_unreadable")
        else:
            self._pending.set(waiting)
            self._oldest_pending.set(oldest.timestamp())

    async def _lock_the_due(self, unit: UnitOfWork) -> list[_Pending]:
        """The oldest events that are due, locked: no other relay's pass takes them until this one ends.

        Due by ``now()``, the start of the transaction, not ``clock_timestamp()``: PostgreSQL can test a
        stable value inside the index (see ``ix_outbox_due``) and not a volatile one. An event that becomes
        due in the few milliseconds before this statement waits for the next pass.
        """
        rows = await unit.connection.execute(
            select(OutboxRecord.id, OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data)
            .where(OutboxRecord.next_attempt_at <= func.now())
            .order_by(_RETRIED, OutboxRecord.created_at, OutboxRecord.id)
            .limit(self._batch)
            .with_for_update(skip_locked=True)
        )
        return [_Pending(event_id, subject, headers, data) for event_id, subject, headers, data in rows]

    async def _publish(
        self, jetstream: JetStreamContext, events: Sequence[_Pending]
    ) -> list[BaseException | None]:
        """Publish every event of the batch at once, then await every acknowledgement.

        For each event, in the batch's order: ``None`` once JetStream acknowledged it, else why it did not.

        The publishes start in the batch's order, and NATS receives them in that order in practice, by how
        nats-py writes to its buffer: nothing in its interface promises it, and a test pins it.
        """
        outcomes = await asyncio.gather(
            *(
                jetstream.publish(
                    event.subject, event.data, timeout=self._publish_timeout, headers=event.headers
                )
                for event in events
            ),
            return_exceptions=True,
        )
        return [outcome if isinstance(outcome, BaseException) else None for outcome in outcomes]

    async def _wait_for_a_commit(self, seconds: float) -> None:
        """Return when a commit of this process wakes the relay, or after ``seconds``."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._wake.wait()
        self._wake.clear()
