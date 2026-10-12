"""Transactional outbox: an event commits with the state change it announces, then a relay publishes it.

The relay publishes a batch and waits for JetStream's acknowledgements inside the transaction that locks
the rows (``FOR UPDATE SKIP LOCKED``), then deletes them. If publishing fails, the transaction rolls back and
the rows stay: an event leaves the table only after JetStream has stored it. One published twice (a crash
between the acknowledgement and the commit, or a batch retried after a failure) is dropped by JetStream's
de-duplication (``Nats-Msg-Id``) within the stream's window, and by the consumer's inbox after that.

A commit wakes the relay of the same process at once; rows written by other replicas are picked up within
``idle`` seconds by any relay. Rows go out oldest first by event time. A failed batch is retried whole, so an
event can follow ones published after it, and replicas take batches in turn: consumers do not rely on the
order of unrelated events.
"""

import asyncio
import contextlib
from dataclasses import dataclass, replace
from datetime import datetime
from math import inf
from typing import ClassVar, Final, Never, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Iterator, Sequence

import structlog
from prometheus_client import Counter
from sqlalchemy import DateTime, LargeBinary, delete, insert, select
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
"""Seconds a pass waits for the acknowledgements of its batch. The rows stay locked and the session idle in
its transaction meanwhile: well below ``idle_in_transaction_session_timeout`` (60 seconds by default)."""
MAX_RETRY_DELAY: Final = 30.0
"""The longest the relay waits after a failure: a recovered NATS is found again within this."""
_logger = structlog.stdlib.get_logger(__name__)

type Sleep = Callable[[float], Awaitable[None]]
"""Waits that many seconds. A test passes its own, so that no test waits."""


class InvalidOutboxOptionError(LedgerError):
    """An outbox option that cannot work. A mistake in the code or the configuration, never client input."""

    code: ClassVar[str] = "invalid_outbox_option"

    def __init__(self, *, option: str, requirement: str, value: object) -> None:
        super().__init__(f"outbox option {option} must be {requirement}, not {described(value)}")


class PublishFailedError(LedgerError):
    """JetStream did not acknowledge an event of the batch; the batch was rolled back and stays in the outbox.

    Names the event and where it goes, never what it says. The cause is the client's own error.
    """

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "outbox_publish_failed"

    def __init__(self, *, event_id: UUID, subject: str) -> None:
        super().__init__(f"event {event_id} was not acknowledged by JetStream on subject {excerpt(subject)}")


def _require_seconds(option: str, value: float, *, ceiling: float | None = None) -> None:
    """Refuse a wait that is not a positive, finite number of seconds, or is longer than ``ceiling``."""
    if not 0 < value < inf:
        raise InvalidOutboxOptionError(
            option=option, requirement="a positive, finite number of seconds", value=value
        )
    if ceiling is not None and value > ceiling:
        raise InvalidOutboxOptionError(option=option, requirement=f"at most {ceiling} seconds", value=value)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


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
    acknowledgements; ``sleep`` waits between attempts while publishing fails.
    """

    __slots__ = ("_batch", "_publish_timeout", "_published", "_sleep", "_tracing", "_wake")

    def __init__(
        self,
        *,
        tracing: Tracing,
        registry: CollectorRegistry,
        batch: int = 100,
        publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not 1 <= batch <= MAX_BATCH:
            raise InvalidOutboxOptionError(
                option="batch", requirement=f"between 1 and {MAX_BATCH}", value=batch
            )
        _require_seconds("publish_timeout", publish_timeout)
        self._tracing = tracing
        self._batch = batch
        self._publish_timeout = publish_timeout
        self._sleep = sleep
        self._wake = asyncio.Event()
        self._published = Counter(
            "ledger_outbox_published", "Events the relay published to JetStream.", registry=registry
        )

    async def add(self, unit: UnitOfWork, envelope: Envelope, *, subject: str) -> None:
        """Store ``envelope`` for ``subject`` in ``unit``'s transaction; the relay wakes when it commits.

        An envelope that carries no trace joins the active one, if there is one.
        """
        traced = (
            envelope
            if envelope.traceparent is not None
            else replace(envelope, traceparent=self._tracing.current_traceparent())
        )
        await unit.connection.execute(
            insert(OutboxRecord).values(
                id=traced.id,
                subject=subject,
                headers=traced.headers(),
                data=traced.data,
                created_at=traced.time,
            )
        )
        unit.after_commit(self._wake.set)

    async def relay_once(self, database: Database, jetstream: JetStreamContext) -> int:
        """Publish up to one batch of pending events, oldest first, and remove them; how many went out.

        The batch is published at once and all acknowledgements awaited together, so the transaction (and the
        row locks in it) lasts one ``publish_timeout`` however many events there are. When any publish fails,
        once all have ended, the first in the batch's order is raised as ``PublishFailedError`` and nothing
        is removed.
        """
        async with database.transaction(None) as unit:
            events = await self._lock_the_oldest(unit)
            if not events:
                return 0
            await self._publish(jetstream, events)
            await unit.connection.execute(
                delete(OutboxRecord).where(OutboxRecord.id.in_([event.id for event in events]))
            )
        self._published.inc(len(events))
        return len(events)

    async def run(
        self, database: Database, jetstream: JetStreamContext, *, idle: float = 1.0, retry: float = 1.0
    ) -> Never:
        """Relay forever: at once after a local commit, every ``idle`` seconds otherwise.

        A failed pass is logged and tried again after ``retry`` seconds, then twice that, and so on up to
        ``MAX_RETRY_DELAY``; a pass that works starts the delays over. Stops only when cancelled.
        """
        _require_seconds("idle", idle)
        _require_seconds("retry", retry, ceiling=MAX_RETRY_DELAY)
        delays = _delays(retry)
        while True:
            try:
                sent = await self.relay_once(database, jetstream)
            except Exception:
                delay = next(delays)
                _logger.exception("outbox.relay_failed", retry_in=delay)
                await self._sleep(delay)
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

    async def _publish(self, jetstream: JetStreamContext, events: Sequence[_Pending]) -> None:
        """Publish every event of the batch at once, then await every acknowledgement.

        The publishes start in the batch's order, which is the order NATS receives them in on one connection.
        All have ended before the first failure, in that order, is raised.
        """
        outcomes = await asyncio.gather(
            *(self._publish_one(jetstream, event) for event in events), return_exceptions=True
        )
        for outcome in outcomes:
            if outcome is not None:
                raise outcome

    async def _publish_one(self, jetstream: JetStreamContext, event: _Pending) -> None:
        try:
            await jetstream.publish(
                event.subject, event.data, timeout=self._publish_timeout, headers=event.headers
            )
        except Exception as error:
            raise PublishFailedError(event_id=event.id, subject=event.subject) from error

    async def _wait_for_a_commit(self, seconds: float) -> None:
        """Return when a commit of this process wakes the relay, or after ``seconds``."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._wake.wait()
        self._wake.clear()
