"""Transactional outbox: an event commits with the state change it announces, then a relay publishes it.

The relay publishes a batch and waits for JetStream's acknowledgements inside the transaction that locks
the rows (``FOR UPDATE SKIP LOCKED``), then deletes the rows JetStream acknowledged and commits: an event
leaves the table only after JetStream has stored it. One that was not acknowledged stays for the next pass
and does not hold back the others: a pass removes every acknowledged event, then reports the failure. One
published twice (a crash between the acknowledgement and the commit, or a retry) is dropped by JetStream's
de-duplication (``Nats-Msg-Id``) within the stream's window, and by the consumer's inbox after that.

A commit wakes the relay of the same process at once; rows written by other replicas are picked up within
``idle`` seconds by any relay. Rows go out oldest first by event time, but order is only kept within a pass
and a relay: an event that failed follows the ones published after it, and replicas take batches in turn.
Consumers rely on the version of the aggregate an event carries, never on the order of unrelated events.

Events nobody could ever publish (a subject NATS cannot route, a message over its size limit) are
refused when they are added, in the caller's transaction. What is left to wait for is a stream that does not
exist yet.
"""

import asyncio
import contextlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import inf
from typing import ClassVar, Final, Never, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import DateTime, Interval, LargeBinary, delete, func, insert, literal, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
lazy from nats.js import JetStreamContext
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, LedgerError, described, excerpt
from ledger_platform.database import PlatformTable
lazy from ledger_contracts.envelope import Envelope
lazy from ledger_platform.database import Database, UnitOfWork
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
MAX_RETRY_DELAY: Final = 30.0
"""The longest the relay waits after a failure: a recovered NATS is found again within this."""
DEFAULT_MAX_PAYLOAD: Final = 1024 * 1024
"""NATS's default ``max_payload``, which counts the headers of a message as well as its body. A service passes
the one its connection reports."""
MAX_SUBJECT_LENGTH: Final = 255
"""Far below NATS's limit (a control line of 4,096 bytes): a longer subject is a mistake."""
_LABEL: Final = r"(?:(?![.*>])[!-~])+"
_SUBJECT: Final = re.compile(rf"{_LABEL}(?:\.{_LABEL})*", re.ASCII)
"""Labels of visible ASCII (no space, no ``.``, no wildcard ``*`` or ``>``) separated by single dots."""
_logger = structlog.stdlib.get_logger(__name__)

type Sleep = Callable[[float], Awaitable[None]]
"""Waits that many seconds. A test passes its own, so that no test waits."""


class InvalidOutboxOptionError(LedgerError):
    """An outbox option that cannot work. A mistake in the code or the configuration, never client input."""

    code: ClassVar[str] = "invalid_outbox_option"

    def __init__(self, *, option: str, requirement: str, value: object) -> None:
        super().__init__(f"outbox option {option} must be {requirement}, not {described(value)}")


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


class PublishFailedError(LedgerError):
    """JetStream did not acknowledge an event of a pass: it stays in the outbox for the next one.

    Raised after the pass has removed the events JetStream did acknowledge. Names the first event that failed,
    in the order of the pass, and never what it says. The cause is the client's own error.
    """

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "outbox_publish_failed"

    event_id: UUID
    subject: str
    acknowledged: int
    """How many events of the pass JetStream did acknowledge (and the pass removed)."""
    failed: int
    """How many events of the pass it did not."""

    def __init__(self, *, event_id: UUID, subject: str, acknowledged: int, failed: int) -> None:
        what = f"event {event_id} was not acknowledged by JetStream on subject {excerpt(subject)}"
        super().__init__(f"{what}: {failed} events of the pass failed and {acknowledged} went out")
        self.event_id = event_id
        self.subject = subject
        self.acknowledged = acknowledged
        self.failed = failed


def _require_seconds(option: str, value: float, *, ceiling: float | None = None) -> None:
    """Refuse a wait that is not a positive, finite number of seconds, or is longer than ``ceiling``."""
    if not 0 < value < inf:
        raise InvalidOutboxOptionError(
            option=option, requirement="a positive, finite number of seconds", value=value
        )
    if ceiling is not None and value > ceiling:
        raise InvalidOutboxOptionError(option=option, requirement=f"at most {ceiling} seconds", value=value)


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


def _delays(first: float) -> Iterator[float]:
    """``first``, then twice the one before, never more than ``MAX_RETRY_DELAY``."""
    delay = first
    while True:
        yield delay
        delay = min(delay * 2, MAX_RETRY_DELAY)


def _event_of(error: Exception) -> dict[str, str]:
    """The log fields that name the event a failure is about; none when it is about no event."""
    if isinstance(error, PublishFailedError):
        return {"event_id": str(error.event_id), "subject": error.subject}
    return {}


def _got_events_out(error: Exception) -> bool:
    """Whether the pass that failed with ``error`` still got other events out: NATS is not the problem."""
    return isinstance(error, PublishFailedError) and error.acknowledged > 0


class OutboxRecord(PlatformTable):
    """An event committed but not yet acknowledged by JetStream."""

    __tablename__: str = "outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column()
    headers: Mapped[dict[str, str]] = mapped_column(JSONB)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    """The event's own time, which orders the relay's reads: the sender's clock, so skewed at worst."""
    stored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """When the event entered the outbox, by the database's clock: what the backlog's age is measured from."""


_BACKLOG: Final = select(
    func.count(),
    type_coerce(
        func.coalesce(func.clock_timestamp() - func.min(OutboxRecord.stored_at), literal(timedelta(0))),
        Interval,
    ),
)
"""How many events wait, and how long the oldest has: ``clock_timestamp`` is the time of the statement, which
no committed ``stored_at`` can be later than, so the age is never negative."""


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

    Every pass sets two gauges, the number of events waiting and the age in seconds of the oldest (from the
    database's clock), and counts the publishes that were not acknowledged.
    """

    __slots__ = (
        "_batch",
        "_max_payload",
        "_oldest_pending_age",
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
        _require_seconds("publish_timeout", publish_timeout, ceiling=MAX_PUBLISH_TIMEOUT)
        if max_payload < 1:
            raise InvalidOutboxOptionError(option="max_payload", requirement="at least 1", value=max_payload)
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
        self._oldest_pending_age = Gauge(
            "ledger_outbox_oldest_pending_age_seconds",
            "Seconds since the oldest pending event was stored; 0 when there is none.",
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

    async def relay_once(self, database: Database, jetstream: JetStreamContext) -> int:
        """Publish up to one batch of pending events, oldest first, and remove those acknowledged; how many.

        The batch is published at once and all acknowledgements awaited together, so the transaction (and the
        row locks in it) lasts one ``publish_timeout`` however many events there are. Every acknowledged event
        is removed, whatever happened to the others. When any publish failed, the first of them in the batch's
        order is then raised as ``PublishFailedError``; the failed events stay for the next pass.
        """
        async with database.transaction(None) as unit:
            events = await self._lock_the_oldest(unit)
            reasons = await self._publish(jetstream, events)
            acknowledged = [event for event, reason in zip(events, reasons, strict=True) if reason is None]
            failures = [
                (event, reason) for event, reason in zip(events, reasons, strict=True) if reason is not None
            ]
            if acknowledged:
                await unit.connection.execute(
                    delete(OutboxRecord).where(OutboxRecord.id.in_([event.id for event in acknowledged]))
                )
            waiting, oldest_age = await self._backlog(unit)
        self._published.inc(len(acknowledged))
        self._publish_failed.inc(len(failures))
        self._pending.set(waiting)
        self._oldest_pending_age.set(oldest_age)
        if failures:
            event, reason = failures[0]
            raise PublishFailedError(
                event_id=event.id, subject=event.subject, acknowledged=len(acknowledged), failed=len(failures)
            ) from reason
        return len(acknowledged)

    async def run(
        self,
        database: Database,
        jetstream: JetStreamContext,
        *,
        idle: float = 1.0,
        retry: float = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> Never:
        """Relay forever: at once after a local commit, every ``idle`` seconds otherwise.

        A failed pass is logged and tried again after ``retry`` seconds, then twice that, and so on up to
        ``MAX_RETRY_DELAY``. A pass that works starts the delays over, and so does one that got other events
        out in spite of a failure: an event stuck behind a missing stream must not slow the rest down.
        Stops only when cancelled. ``sleep`` waits between attempts after a failure.
        """
        _require_seconds("idle", idle)
        _require_seconds("retry", retry, ceiling=MAX_RETRY_DELAY)
        delays = _delays(retry)
        while True:
            try:
                sent = await self.relay_once(database, jetstream)
            except Exception as error:
                if _got_events_out(error):
                    delays = _delays(retry)
                delay = next(delays)
                _logger.exception("outbox.relay_failed", retry_in=delay, **_event_of(error))
                await sleep(delay)
                continue
            delays = _delays(retry)
            if sent < self._batch:
                await self._wait_for_a_commit(idle)

    async def _lock_the_oldest(self, unit: UnitOfWork) -> list[_Pending]:
        """The oldest events, locked: another relay's pass skips them until this transaction ends."""
        rows = await unit.connection.execute(
            select(OutboxRecord.id, OutboxRecord.subject, OutboxRecord.headers, OutboxRecord.data)
            .order_by(OutboxRecord.created_at, OutboxRecord.id)
            .limit(self._batch)
            .with_for_update(skip_locked=True)
        )
        return [_Pending(event_id, subject, headers, data) for event_id, subject, headers, data in rows]

    async def _backlog(self, unit: UnitOfWork) -> tuple[int, float]:
        """How many events wait (locked and failed ones too), and the age in seconds of the oldest."""
        waiting, oldest_age = (await unit.connection.execute(_BACKLOG)).one()
        return waiting, oldest_age.total_seconds()

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
