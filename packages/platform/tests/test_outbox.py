import asyncio
import contextlib
import traceback
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from math import inf, nan
from typing import Never, Protocol
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
lazy from uuid import UUID

import pytest
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from prometheus_client import CollectorRegistry
from sqlalchemy import Boolean, DateTime, String, column, delete, func, select, text, type_coerce, update
from sqlalchemy.event import listen, remove
from structlog.testing import capture_logs
lazy from nats.aio.client import Client

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform import outbox as outbox_module
from ledger_platform.database import Database
from ledger_platform.outbox import (
    BACKLOG_EVERY,
    DEFAULT_MAX_PAYLOAD,
    MAX_BATCH,
    MAX_IDLE,
    MAX_PUBLISH_TIMEOUT,
    MAX_RETRY_DELAY,
    MIN_RETRY_DELAY,
    STREAMS_ENSURED_EVERY,
    EventTooLargeError,
    InvalidOutboxOptionError,
    InvalidSubjectError,
    Outbox,
    OutboxRecord,
    PublishFailedError,
    RelayPace,
)
from ledger_platform.streams import StreamSpec, ensure_stream
lazy from ledger_platform.tracing import Tracing

STREAM = StreamSpec(name="TEST", subjects=("test.>",))
STREAM_EVENTS = frozenset({"outbox.streams_ensured", "outbox.streams_not_ensured"})
"""What a relay logs when it declares its service's streams again."""
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

type Eventually = Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]
"""The ``eventually`` fixture of conftest.py (basedpyright cannot resolve an import of a conftest)."""


def event() -> Envelope:
    return Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=None)


def wire_size(envelope: Envelope) -> int:
    """The bytes NATS counts against ``max_payload``: the header block as it is sent, then the body."""
    lines = sum(
        len(name) + len(": ") + len(value) + len("\r\n") for name, value in envelope.headers().items()
    )
    return len("NATS/1.0\r\n") + lines + len("\r\n") + len(envelope.data)


def event_of_size(size: int) -> Envelope:
    """An event that takes ``size`` bytes on the wire."""
    bare = replace(event(), data=b"")
    return replace(bare, data=b"x" * (size - wire_size(bare)))


@pytest.fixture
def outbox(tracing: Tracing, registry: CollectorRegistry) -> Outbox:
    return Outbox(tracing=tracing, registry=registry, batch=10)


async def pending(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(OutboxRecord))).scalar_one()


async def stored(jetstream: JetStreamContext, stream: str = "TEST") -> int:
    return (await jetstream.stream_info(stream)).state.messages


async def commit(
    database: Database,
    outbox: Outbox,
    envelope: Envelope | None = None,
    *,
    subject: str = "test.created",
    tenant: Tenant | None = None,
) -> None:
    """Write an event in a transaction of its own."""
    async with database.transaction(tenant) as unit:
        await outbox.add(unit, event() if envelope is None else envelope, subject=subject)


def sample(registry: CollectorRegistry, name: str) -> float:
    value = registry.get_sample_value(name)
    assert value is not None
    return value


async def reached(registry: CollectorRegistry, name: str, at_least: float) -> bool:
    return (registry.get_sample_value(name) or 0) >= at_least


async def make_due(database: Database) -> None:
    """Let time pass for the events that wait for their next attempt."""
    async with database.transaction(None) as unit:
        await unit.connection.execute(
            update(OutboxRecord).values(next_attempt_at=func.now() - timedelta(seconds=1))
        )


async def attempts_and_due(database: Database, event_id: UUID) -> tuple[int, float]:
    """How often publishing the event failed, and in how many seconds it is due again (database clock)."""
    now = type_coerce(func.clock_timestamp(), DateTime(timezone=True))
    async with database.transaction(None) as unit:
        row = await unit.connection.execute(
            select(OutboxRecord.attempts, OutboxRecord.next_attempt_at, now).where(
                OutboxRecord.id == event_id
            )
        )
        attempts, due, at = row.one()
    return attempts, (due - at).total_seconds()


async def database_time(database: Database) -> float:
    """The database's clock as a Unix time, to compare with what the database dated."""
    async with database.transaction(None) as unit:
        now = type_coerce(func.clock_timestamp(), DateTime(timezone=True))
        at = (await unit.connection.execute(select(now))).scalar_one()
    return at.timestamp()


async def drained(database: Database) -> bool:
    return await pending(database) == 0


async def pending_is(database: Database, count: int) -> bool:
    """Whether the outbox holds ``count`` events: what is left once the relay's pass has committed.

    The relay publishes and waits for the acknowledgements inside the transaction that deletes the rows, so
    JetStream holds an event before the outbox lets go of it. A test that stops the relay on seeing the event
    in JetStream can roll that transaction back, and the rows are still there.
    """
    return await pending(database) == count


async def unlocked(database: Database, count: int) -> bool:
    """Whether exactly ``count`` pending events are free of locks: what a pass skipping locked rows finds."""
    async with database.transaction(None) as unit:
        free = (
            await unit.connection.execute(select(OutboxRecord.id).with_for_update(skip_locked=True))
        ).all()
    return len(free) == count


async def delivered(jetstream: JetStreamContext, count: int) -> bool:
    return await stored(jetstream) >= count


async def published(jetstream: JetStreamContext, count: int) -> list[Envelope]:
    """The first ``count`` events of ``TEST``, in the order the stream stored them, read back from the headers
    and the body JetStream kept."""
    consumer = await jetstream.pull_subscribe("test.>", stream="TEST")
    messages = await consumer.fetch(count, timeout=5)
    events: list[Envelope] = []
    for message in messages:
        assert message.headers is not None
        events.append(Envelope.from_headers(message.headers, message.data))
    return events


@asynccontextmanager
async def relaying(relay: Coroutine[object, object, Never]) -> AsyncGenerator[None]:
    """The relay (``outbox.run(...)``) in the background, cancelled and awaited on exit: none outlives a test.

    A relay left running would hold locks the fixtures' ``DROP TABLE`` waits for, and be destroyed pending.
    """
    task = asyncio.create_task(relay)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class Subscribing(Protocol):
    """A NATS connection as ``silent`` uses it: nats-py leaves the type of ``subscribe``'s ``future`` open."""

    async def subscribe(self, subject: str) -> object: ...


async def silent(connection: Subscribing, subject: str) -> None:
    """Listen on ``subject`` and never reply: a JetStream publish to it waits for an ack forever."""
    await connection.subscribe(subject)


async def test_a_committed_event_is_published_once_then_removed(
    database: Database, jetstream: JetStreamContext, outbox: Outbox, registry: CollectorRegistry
) -> None:
    await ensure_stream(jetstream, STREAM)
    envelope = event()
    async with database.transaction(None) as unit:
        await outbox.add(unit, envelope, subject="test.created")
    assert await pending(database) == 1

    assert await outbox.relay_once(database, jetstream) == 1
    assert await pending(database) == 0
    assert await outbox.relay_once(database, jetstream) == 0

    assert await published(jetstream, 1) == [envelope]
    assert await stored(jetstream) == 1
    assert registry.get_sample_value("ledger_outbox_published_total") == 1


def abort() -> None:
    raise LookupError


async def add_then_fail(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
        abort()


async def test_an_event_in_a_rolled_back_transaction_never_exists(database: Database, outbox: Outbox) -> None:
    with pytest.raises(LookupError):
        await add_then_fail(database, outbox)
    assert await pending(database) == 0


async def stored_headers(database: Database) -> dict[str, str]:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(OutboxRecord.headers))).scalar_one()


async def test_the_active_trace_travels_with_the_event(
    database: Database, outbox: Outbox, tracing: Tracing
) -> None:
    with tracing.tracer.start_as_current_span("request"):
        active = tracing.current_traceparent()
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
    assert active is not None
    assert (await stored_headers(database))["traceparent"] == active


async def test_a_trace_the_event_already_carries_is_kept(
    database: Database, outbox: Outbox, tracing: Tracing
) -> None:
    with tracing.tracer.start_as_current_span("request"):
        async with database.transaction(None) as unit:
            await outbox.add(unit, replace(event(), traceparent=TRACEPARENT), subject="test.created")
    assert (await stored_headers(database))["traceparent"] == TRACEPARENT


async def test_outside_any_trace_the_event_carries_none(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
    assert "traceparent" not in await stored_headers(database)


async def test_a_batch_goes_out_oldest_first_and_in_order(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    """Twelve events, three to each second, written newest first: the stream stores them oldest first.

    Events of the same second keep the order of their ids, which is the order they were made in.
    """
    await ensure_stream(jetstream, STREAM)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    events = [replace(event(), time=start + timedelta(seconds=n // 3)) for n in range(12)]
    async with database.transaction(None) as unit:
        for envelope in reversed(events):
            await outbox.add(unit, envelope, subject="test.created")

    first_pass = await outbox.relay_once(database, jetstream)
    second_pass = await outbox.relay_once(database, jetstream)
    assert (first_pass, second_pass) == (10, 2)

    assert await published(jetstream, 12) == events


async def test_a_batch_goes_out_in_order_on_a_connection_that_has_sent_nothing_yet(
    database: Database,
    jetstream: JetStreamContext,
    outbox: Outbox,
    connect: Callable[[], Awaitable[Client]],
) -> None:
    """A connection sets up its reply subscription at its first request: events must not overtake."""
    await ensure_stream(jetstream, STREAM)
    events = [event() for _ in range(10)]
    async with database.transaction(None) as unit:
        for envelope in events:
            await outbox.add(unit, envelope, subject="test.created")

    fresh = JetStreamContext(await connect())
    assert await outbox.relay_once(database, fresh) == 10

    assert await published(jetstream, 10) == events


async def test_events_wait_while_publishing_fails_and_go_out_once_it_works(
    database: Database, jetstream: JetStreamContext, outbox: Outbox, eventually: Eventually
) -> None:
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=0.05, retry=0.05))):
        await asyncio.sleep(0.3)
        assert await pending(database) == 1  # no stream yet: every publish fails

        await ensure_stream(jetstream, STREAM)
        await eventually(partial(drained, database))
    assert await stored(jetstream) == 1


async def test_a_failure_names_the_event_and_nothing_of_its_content(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
    registry: CollectorRegistry,
) -> None:
    outbox = Outbox(tracing=tracing, registry=registry, publish_timeout=0.1)
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
    envelope = Envelope.wrap(Money(amount="31337", currency="DOP"), source="test", tenant=tenant)
    async with database.transaction(None) as unit:
        await outbox.add(unit, envelope, subject="test.silent")

    await silent(await connect(), "test.silent")
    async with asyncio.timeout(3):  # nats-py would wait 5 seconds: only the explicit timeout fits
        with pytest.raises(PublishFailedError) as failed:
            await outbox.relay_once(database, jetstream)

    assert isinstance(failed.value.__cause__, NatsTimeoutError)
    report = "".join(traceback.format_exception(failed.value))
    assert str(envelope.id) in report
    assert "test.silent" in report
    for content in ("31337", "DOP", str(tenant.organization), str(tenant.company)):
        assert content not in report
    assert await pending(database) == 1


async def test_the_relay_logs_a_failed_publish_and_names_the_event(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry)
    first = event()
    await commit(database, outbox, first, subject="quiet.a")  # no stream for these two
    await commit(database, outbox)
    await commit(database, outbox, subject="quiet.c")

    with capture_logs() as logs:
        async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
            await eventually(partial(reached, registry, "ledger_outbox_publish_failed_total", 2))

    assert logs == [
        {
            "event": "outbox.publish_failed",
            "log_level": "warning",
            "event_id": str(first.id),
            "subject": "quiet.a",
            "error": "nats.js.errors.NoStreamResponseError",
            "acknowledged": 1,
            "failed": 2,
        }
    ]


async def test_the_relay_backs_off_while_the_database_fails_up_to_a_ceiling(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    delays: list[float] = []
    enough = asyncio.Event()

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 8:
            enough.set()
            await asyncio.Event().wait()  # the test ends the relay

    outbox = Outbox(tracing=tracing, registry=registry)
    await rename_outbox(database, "outbox", "outbox_away")  # every statement of the relay fails
    try:
        async with relaying(outbox.run(database, jetstream, pace=RelayPace(retry=1), sleep=sleep)):
            async with asyncio.timeout(10):
                await enough.wait()
    finally:
        await rename_outbox(database, "outbox_away", "outbox", if_exists=True)

    assert MAX_RETRY_DELAY == 30
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert sample(registry, "ledger_outbox_failed_passes_total") == 8


async def test_a_pass_that_works_starts_the_delays_over(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    await ensure_stream(jetstream, STREAM)
    delays: list[float] = []
    restored, failed_again = asyncio.Event(), asyncio.Event()

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            await rename_outbox(database, "outbox_away", "outbox")  # the database is back
            restored.set()
        elif len(delays) == 4:
            failed_again.set()
            await asyncio.Event().wait()  # the test ends the relay

    outbox = Outbox(tracing=tracing, registry=registry)
    await commit(database, outbox)
    await rename_outbox(database, "outbox", "outbox_away")
    try:
        async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=0.05, retry=1), sleep=sleep)):
            async with asyncio.timeout(5):
                await restored.wait()
            await eventually(partial(drained, database))  # three failed passes, then the event goes out
            await rename_outbox(database, "outbox", "outbox_away")
            async with asyncio.timeout(5):
                await failed_again.wait()
    finally:
        await rename_outbox(database, "outbox_away", "outbox", if_exists=True)

    assert delays == [1, 2, 4, 1]


async def test_a_commit_wakes_the_relay(
    database: Database, jetstream: JetStreamContext, outbox: Outbox, eventually: Eventually
) -> None:
    await ensure_stream(jetstream, STREAM)
    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await asyncio.sleep(0.1)
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
        await eventually(partial(delivered, jetstream, 1))


async def test_an_event_another_replica_wrote_is_found_within_idle(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, eventually: Eventually
) -> None:
    await ensure_stream(jetstream, STREAM)
    relay = Outbox(tracing=tracing, registry=CollectorRegistry())
    other_replica = Outbox(tracing=tracing, registry=CollectorRegistry())
    async with relaying(relay.run(database, jetstream, pace=RelayPace(idle=0.05))):
        await asyncio.sleep(0.1)
        async with database.transaction(None) as unit:
            await other_replica.add(
                unit, event(), subject="test.created"
            )  # wakes its own relay, not this one
        await eventually(partial(delivered, jetstream, 1))


async def test_more_events_than_a_batch_go_out_without_waiting(
    database: Database, jetstream: JetStreamContext, outbox: Outbox, eventually: Eventually
) -> None:
    await ensure_stream(jetstream, STREAM)
    async with database.transaction(None) as unit:
        for _ in range(25):
            await outbox.add(unit, event(), subject="test.created")
    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await eventually(partial(delivered, jetstream, 25))


async def test_a_relay_does_not_wait_for_events_another_one_holds(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
    eventually: Eventually,
) -> None:
    """The oldest five go to a subject nobody acknowledges: that relay holds them, waiting. Another replica's
    relay publishes the rest at once instead of queueing behind its locks."""
    await ensure_stream(jetstream, STREAM)
    await silent(await connect(), "quiet.created")
    stalled = Outbox(
        tracing=tracing, registry=CollectorRegistry(), batch=5, publish_timeout=MAX_PUBLISH_TIMEOUT
    )
    other_replica = Outbox(tracing=tracing, registry=CollectorRegistry(), batch=5)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with database.transaction(None) as unit:
        for n in range(10):
            subject = "quiet.created" if n < 5 else "test.created"
            await stalled.add(unit, replace(event(), time=start + timedelta(seconds=n)), subject=subject)

    async with relaying(stalled.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await eventually(partial(unlocked, database, 5))

        assert await other_replica.relay_once(database, jetstream) == 5

        assert await stored(jetstream) == 5
        assert await pending(database) == 5


async def test_stopping_the_relay_mid_publish_releases_the_events(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
    eventually: Eventually,
) -> None:
    outbox = Outbox(tracing=tracing, registry=CollectorRegistry(), publish_timeout=MAX_PUBLISH_TIMEOUT)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.silent")

    await silent(await connect(), "test.silent")
    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await eventually(partial(unlocked, database, 0))  # the relay holds the event, waiting for an ack
    assert await pending(database) == 1

    await ensure_stream(jetstream, STREAM)
    assert await outbox.relay_once(database, jetstream) == 1  # a locked event would have been skipped


@pytest.mark.parametrize("batch", [0, -1, MAX_BATCH + 1])
def test_a_batch_outside_its_bounds_is_refused(
    batch: int, tracing: Tracing, registry: CollectorRegistry
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="batch"):
        Outbox(tracing=tracing, registry=registry, batch=batch)


@pytest.mark.parametrize("timeout", [0, -1, nan, inf, MAX_PUBLISH_TIMEOUT + 1])
def test_a_publish_timeout_that_is_not_a_positive_number_is_refused(
    timeout: float, tracing: Tracing, registry: CollectorRegistry
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="publish_timeout"):
        Outbox(tracing=tracing, registry=registry, publish_timeout=timeout)


@pytest.mark.parametrize("idle", [0, -1, nan, inf, MAX_IDLE + 1])
def test_an_idle_wait_that_is_not_a_positive_number_is_refused(idle: float) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="idle"):
        RelayPace(idle=idle)


@pytest.mark.parametrize("retry", [0, -1, nan, inf, MIN_RETRY_DELAY / 2, MAX_RETRY_DELAY + 1])
def test_a_retry_delay_outside_its_bounds_is_refused(retry: float) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="retry"):
        RelayPace(retry=retry)


async def test_an_event_that_cannot_go_out_does_not_hold_back_the_ones_around_it(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    """The middle event of three has no stream to go to: the other two go out and leave the table."""
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=10)
    first, second, third = event(), event(), event()
    async with database.transaction(None) as unit:
        await outbox.add(unit, first, subject="test.a")
        await outbox.add(unit, second, subject="quiet.b")
        await outbox.add(unit, third, subject="test.c")

    with pytest.raises(PublishFailedError, match=str(second.id)) as failed:
        await outbox.relay_once(database, jetstream)
    assert (failed.value.event_id, failed.value.subject) == (second.id, "quiet.b")
    assert (failed.value.acknowledged, failed.value.failed) == (2, 1)
    assert await stored(jetstream) == 2
    assert await pending(database) == 1

    await ensure_stream(jetstream, StreamSpec(name="QUIET", subjects=("quiet.>",)))
    assert await outbox.relay_once(database, jetstream) == 0  # b waits for its next attempt
    await make_due(database)
    assert await outbox.relay_once(database, jetstream) == 1
    assert await stored(jetstream) == 2
    assert await stored(jetstream, "QUIET") == 1
    assert await pending(database) == 0
    assert registry.get_sample_value("ledger_outbox_published_total") == 3


async def test_an_event_published_twice_is_stored_once(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    """A relay that dies after the acknowledgement and before its commit leaves the row: the next pass
    publishes the event again, and JetStream drops it by its ``Nats-Msg-Id``."""
    await ensure_stream(jetstream, STREAM)
    envelope = event()
    async with database.transaction(None) as unit:
        await outbox.add(unit, envelope, subject="test.created")
    await jetstream.publish("test.created", envelope.data, headers=envelope.headers())  # the dead relay's

    assert await outbox.relay_once(database, jetstream) == 1

    assert await pending(database) == 0
    assert await stored(jetstream) == 1


async def test_a_failing_event_does_not_block_the_relay(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """The oldest event has nowhere to go; five more are behind it, and the relay takes two at a time."""
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=2)
    await commit(database, outbox, subject="quiet.poison")
    for _ in range(5):
        await commit(database, outbox)

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE, retry=0.05))):
        await eventually(partial(delivered, jetstream, 5))
        await eventually(partial(pending_is, database, 1))  # the commit that removes the five has happened

    assert await pending(database) == 1
    assert sample(registry, "ledger_outbox_publish_failed_total") >= 1


async def rename_outbox(database: Database, old: str, new: str, *, if_exists: bool = False) -> None:
    """Take the table away from (or give it back to) the relay: fixed names, never input."""
    exists = "IF EXISTS " if if_exists else ""
    async with database.engine.begin() as connection:
        await connection.execute(text(f"ALTER TABLE {exists}{old} RENAME TO {new}"))


async def test_the_relay_survives_the_database_failing(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    await ensure_stream(jetstream, STREAM)
    delays: list[float] = []
    restored = asyncio.Event()

    async def sleep(delay: float) -> None:
        delays.append(delay)
        await rename_outbox(database, "outbox_away", "outbox")  # the database is back
        restored.set()

    outbox = Outbox(tracing=tracing, registry=registry)
    await commit(database, outbox)
    await rename_outbox(database, "outbox", "outbox_away")  # every statement of the relay fails
    try:
        with capture_logs() as logs:
            async with relaying(outbox.run(database, jetstream, pace=RelayPace(retry=1), sleep=sleep)):
                async with asyncio.timeout(5):
                    await restored.wait()
                await eventually(partial(drained, database))
    finally:
        await rename_outbox(database, "outbox_away", "outbox", if_exists=True)

    assert delays == [1]
    assert logs == [{"event": "outbox.relay_failed", "log_level": "error", "exc_info": True, "retry_in": 1}]
    assert sample(registry, "ledger_outbox_failed_passes_total") == 1
    assert await stored(jetstream) == 1


async def test_the_outbox_is_outside_row_level_security(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    """A company's transaction writes the event; the relay, which has no tenant, still sees and sends it."""
    await ensure_stream(jetstream, STREAM)
    tenant = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
    envelope = Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=tenant)
    await commit(database, outbox, envelope, tenant=tenant)

    assert await outbox.relay_once(database, jetstream) == 1

    assert await published(jetstream, 1) == [envelope]


@pytest.mark.parametrize(
    "subject",
    [
        "",
        " ",
        "a b",
        "a..b",
        ".a",
        "a.",
        "a.*",
        "a.>",
        "*",
        ">",
        "a*b",
        "a>b",
        "a\r\nb",
        "a\tb",
        "é.created",
        "x" * 256,
    ],
)
async def test_a_subject_nothing_can_be_published_to_is_refused_when_the_event_is_added(
    subject: str, database: Database, outbox: Outbox
) -> None:
    with pytest.raises(InvalidSubjectError) as refused:
        await commit(database, outbox, subject=subject)
    assert len(str(refused.value)) < 200
    assert await pending(database) == 0


@pytest.mark.parametrize(
    "subject", ["a", "test.created", "ledger.invoicing.v1.document_issued", "$JS.a-b_c.1", "x" * 255]
)
async def test_a_subject_nats_accepts_is_stored(subject: str, database: Database, outbox: Outbox) -> None:
    await commit(database, outbox, subject=subject)
    assert await pending(database) == 1


async def test_an_event_as_big_as_the_servers_limit_goes_through_and_one_byte_more_is_refused(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    """The limit counts the headers as well as the body: the real server takes this event, not a byte more."""
    await ensure_stream(jetstream, STREAM)
    await commit(database, outbox, event_of_size(DEFAULT_MAX_PAYLOAD))
    assert await outbox.relay_once(database, jetstream) == 1
    assert await stored(jetstream) == 1

    too_big = event_of_size(DEFAULT_MAX_PAYLOAD + 1)
    with pytest.raises(EventTooLargeError, match=str(too_big.id)):
        await commit(database, outbox, too_big)
    assert await pending(database) == 0


async def test_the_limit_on_an_event_is_the_one_the_outbox_is_given(
    database: Database, tracing: Tracing, registry: CollectorRegistry
) -> None:
    size = wire_size(event_of_size(2048))
    outbox = Outbox(tracing=tracing, registry=registry, max_payload=size)
    await commit(database, outbox, event_of_size(size))
    with pytest.raises(EventTooLargeError, match=f"{size + 1} bytes.*at most {size}"):
        await commit(database, outbox, event_of_size(size + 1))
    assert await pending(database) == 1


async def test_the_backlog_is_dated_from_when_the_events_were_stored(
    database: Database, tracing: Tracing, registry: CollectorRegistry
) -> None:
    """Event times can be backdated or skewed: the oldest event is dated by the database's clock."""
    outbox = Outbox(tracing=tracing, registry=registry)
    backdated = replace(event(), time=datetime.now(UTC) - timedelta(days=1))
    await commit(database, outbox, backdated)
    await commit(database, outbox)

    await outbox.measure_backlog(database)
    assert sample(registry, "ledger_outbox_pending") == 2
    stored_ago = await database_time(database) - sample(
        registry, "ledger_outbox_oldest_pending_timestamp_seconds"
    )
    assert 0 <= stored_ago < 60  # not the day the envelope says

    async with database.transaction(None) as unit:
        await unit.connection.execute(
            update(OutboxRecord)
            .values(stored_at=func.now() - timedelta(seconds=90))
            .where(OutboxRecord.id == backdated.id)
        )
    await outbox.measure_backlog(database)
    stored_ago = await database_time(database) - sample(
        registry, "ledger_outbox_oldest_pending_timestamp_seconds"
    )
    assert 90 <= stored_ago < 150


async def test_an_empty_outbox_has_no_backlog(
    database: Database, outbox: Outbox, registry: CollectorRegistry
) -> None:
    await commit(database, outbox)
    await outbox.measure_backlog(database)
    assert sample(registry, "ledger_outbox_pending") == 1

    async with database.transaction(None) as unit:
        await unit.connection.execute(delete(OutboxRecord))
    await outbox.measure_backlog(database)
    assert sample(registry, "ledger_outbox_pending") == 0
    assert sample(registry, "ledger_outbox_oldest_pending_timestamp_seconds") == 0


async def test_a_backlog_that_cannot_be_read_is_logged_and_changes_nothing(
    database: Database, outbox: Outbox, registry: CollectorRegistry
) -> None:
    await commit(database, outbox)
    await outbox.measure_backlog(database)
    before = sample(registry, "ledger_outbox_oldest_pending_timestamp_seconds")

    await rename_outbox(database, "outbox", "outbox_away")
    try:
        with capture_logs() as logs:
            await outbox.measure_backlog(database)
    finally:
        await rename_outbox(database, "outbox_away", "outbox", if_exists=True)

    assert logs == [{"event": "outbox.backlog_unreadable", "log_level": "error", "exc_info": True}]
    assert sample(registry, "ledger_outbox_pending") == 1
    assert sample(registry, "ledger_outbox_oldest_pending_timestamp_seconds") == before


@pytest.mark.parametrize("max_payload", [0, -1])
def test_a_payload_limit_below_one_byte_is_refused(
    max_payload: int, tracing: Tracing, registry: CollectorRegistry
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="max_payload"):
        Outbox(tracing=tracing, registry=registry, max_payload=max_payload)


async def test_stuck_events_in_numbers_beyond_a_batch_do_not_hold_back_healthy_ones(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Five events with no stream are older than three healthy ones, and a batch is three: the oldest rows of
    every pass are the stuck ones, unless each stuck row keeps to its own schedule."""
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=3)
    for _ in range(5):
        await commit(database, outbox, subject="quiet.stuck")
    for _ in range(3):
        await commit(database, outbox)

    async with relaying(
        outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE, retry=MAX_RETRY_DELAY))
    ):
        await eventually(partial(delivered, jetstream, 3))
        await eventually(partial(pending_is, database, 5))  # the commit that removes the three has happened

    assert await pending(database) == 5
    assert sample(registry, "ledger_outbox_failed_passes_total") == 0


async def test_the_first_failure_in_the_batch_order_is_the_one_reported(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry)
    first, second, third = event(), event(), event()
    await commit(database, outbox, first, subject="quiet.a")
    await commit(database, outbox, second, subject="test.b")
    await commit(database, outbox, third, subject="quiet.c")

    with pytest.raises(PublishFailedError, match=str(first.id)) as failed:
        await outbox.relay_once(database, jetstream)

    assert (failed.value.subject, failed.value.acknowledged, failed.value.failed) == ("quiet.a", 1, 2)
    assert await pending(database) == 2


async def test_a_stuck_event_keeps_to_a_schedule_of_its_own(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    """Tried again after ``retry`` seconds, then twice, then four times as long, never beyond the ceiling."""
    outbox = Outbox(tracing=tracing, registry=registry)
    stuck = event()
    await commit(database, outbox, stuck, subject="quiet.stuck")

    for attempts, expected in enumerate((10, 20, 30, 30, 30), start=1):
        with pytest.raises(PublishFailedError):
            await outbox.relay_once(database, jetstream, retry=10)
        recorded, due = await attempts_and_due(database, stuck.id)
        assert recorded == attempts
        assert expected - 5 < due <= expected
        assert (
            await outbox.relay_once(database, jetstream, retry=10) == 0
        )  # not due: left alone, nothing to report
        await make_due(database)
    assert sample(registry, "ledger_outbox_publish_failed_total") == 5


async def test_a_schedule_does_not_overflow_after_thousands_of_attempts(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    outbox = Outbox(tracing=tracing, registry=registry)
    stuck = event()
    await commit(database, outbox, stuck, subject="quiet.stuck")
    async with database.transaction(None) as unit:
        await unit.connection.execute(update(OutboxRecord).values(attempts=5000))

    for retry in (MIN_RETRY_DELAY, 10, MAX_RETRY_DELAY):
        await make_due(database)
        with pytest.raises(PublishFailedError):
            await outbox.relay_once(database, jetstream, retry=retry)
        _, due = await attempts_and_due(database, stuck.id)
        assert MAX_RETRY_DELAY - 5 < due <= MAX_RETRY_DELAY


async def test_stuck_events_go_out_once_their_stream_appears(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """More than two batches of them, each due again in half a minute: until time passes, nothing moves."""
    outbox = Outbox(tracing=tracing, registry=registry, batch=3)
    for _ in range(7):
        await commit(database, outbox, subject="quiet.stuck")

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=0.05, retry=MAX_RETRY_DELAY))):
        await eventually(partial(reached, registry, "ledger_outbox_publish_failed_total", 7))
        await ensure_stream(jetstream, StreamSpec(name="QUIET", subjects=("quiet.>",)))
        await asyncio.sleep(0.2)
        assert await pending(database) == 7
        await make_due(database)
        await eventually(partial(drained, database))

    assert await stored(jetstream, "QUIET") == 7
    assert sample(registry, "ledger_outbox_publish_failed_total") == 7


async def test_a_full_pass_is_followed_by_another_at_once(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=2)
    for _ in range(9):
        await commit(database, outbox)

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await eventually(partial(delivered, jetstream, 9))


async def gauge_is(registry: CollectorRegistry, name: str, value: float) -> bool:
    return registry.get_sample_value(name) == value


@pytest.fixture
def quick_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    """A relay that measures the backlog every fifth of a second: every fifteen, in production."""
    monkeypatch.setattr(outbox_module, "BACKLOG_EVERY", 0.2)


@pytest.mark.usefixtures("quick_backlog")
async def test_the_relay_measures_the_backlog_after_its_first_pass_and_again_once_the_interval_is_over(
    database: Database,
    jetstream: JetStreamContext,
    outbox: Outbox,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    await commit(database, outbox, subject="quiet.a")

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=0.05))):
        await eventually(partial(gauge_is, registry, "ledger_outbox_pending", 1))
        await commit(database, outbox, subject="quiet.b")
        await eventually(partial(gauge_is, registry, "ledger_outbox_pending", 2))

    assert sample(registry, "ledger_outbox_oldest_pending_timestamp_seconds") > 0


async def test_the_relay_measures_the_backlog_at_most_once_per_interval_however_often_it_polls(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """A count of the whole table every second, on every replica, costs more than a gauge is worth."""
    outbox = Outbox(tracing=tracing, registry=registry)
    await commit(database, outbox, subject="quiet.a")

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=0.05, retry=0.05))):
        await eventually(partial(gauge_is, registry, "ledger_outbox_pending", 1))
        await commit(database, outbox, subject="quiet.b")  # passes every 50 ms from here on
        await eventually(partial(reached, registry, "ledger_outbox_publish_failed_total", 6))

    assert BACKLOG_EVERY == 15
    assert sample(registry, "ledger_outbox_pending") == 1


async def test_the_oldest_event_is_found_through_an_index(database: Database) -> None:
    """``min(stored_at)`` reads one end of ``ix_outbox_stored_at`` instead of the whole table."""
    definitions = select(column("indexdef", String)).select_from(text("pg_indexes"))
    async with database.transaction(None) as unit:
        found = await unit.connection.execute(
            definitions.where(column("indexname", String) == "ix_outbox_stored_at")
        )
        assert found.scalar_one().endswith("(stored_at)")


async def test_the_events_that_are_due_are_read_through_an_index(database: Database) -> None:
    """``ix_outbox_due`` lets PostgreSQL pass over the events that are not due without visiting them."""
    definitions = select(column("indexdef", String)).select_from(text("pg_indexes"))
    async with database.transaction(None) as unit:
        found = await unit.connection.execute(
            definitions.where(column("indexname", String) == "ix_outbox_due")
        )
        definition = found.scalar_one()
    assert definition.endswith("(((attempts > 0)), created_at, id, next_attempt_at)")


async def test_the_zero_of_the_retried_test_is_sent_to_postgresql_as_a_literal(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    """A bound zero would not match the expression of ``ix_outbox_due``: PostgreSQL would read every row."""
    sent: list[str] = []

    def record(*arguments: object) -> None:
        """SQLAlchemy's ``before_cursor_execute``: connection, cursor, statement, parameters, ..."""
        statement = arguments[2]
        assert isinstance(statement, str)
        sent.append(statement)

    listen(database.engine.sync_engine, "before_cursor_execute", record)
    try:
        await outbox.relay_once(database, jetstream)
    finally:
        remove(database.engine.sync_engine, "before_cursor_execute", record)

    (lock,) = (statement for statement in sent if "FOR UPDATE" in statement)
    assert "ORDER BY outbox.attempts > 0, outbox.created_at, outbox.id" in lock


async def test_a_retry_the_pass_cannot_work_with_is_refused_before_anything_is_read(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="retry"):
        await outbox.relay_once(database, jetstream, retry=MAX_RETRY_DELAY + 1)


async def test_the_backlog_is_measured_during_a_run_of_full_passes_too(
    database: Database,
    jetstream: JetStreamContext,
    tracing: Tracing,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Forty events, a batch of one: forty full passes, none of them the last word on the backlog."""
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=1)
    for _ in range(40):
        await commit(database, outbox)

    async with relaying(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE))):
        await eventually(partial(drained, database))

    assert sample(registry, "ledger_outbox_pending") == 39  # measured once, after the first pass


async def test_retried_events_cannot_delay_fresh_ones(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    """Five events that failed before are due again and older than three fresh ones, and a batch is three."""
    await ensure_stream(jetstream, STREAM)
    outbox = Outbox(tracing=tracing, registry=registry, batch=3)
    stuck = [event() for _ in range(5)]
    for envelope in stuck:
        await commit(database, outbox, envelope, subject="quiet.stuck")
    for _ in range(2):  # a batch of three, then the other two: every one of them has failed once
        with pytest.raises(PublishFailedError):
            # The ceiling: the first three are not due again whenever the second pass happens to run
            await outbox.relay_once(database, jetstream, retry=MAX_RETRY_DELAY)
    await make_due(database)
    for _ in range(3):
        await commit(database, outbox)

    assert await outbox.relay_once(database, jetstream) == 3  # the fresh ones, with nothing to report

    assert await stored(jetstream) == 3
    for envelope in stuck:
        assert (await attempts_and_due(database, envelope.id))[0] == 1


async def test_a_stream_deleted_under_the_relay_is_created_again_at_most_once_per_interval(
    database: Database,
    jetstream: JetStreamContext,
    outbox: Outbox,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Someone deleted the service's stream: its events find no stream, and the relay declares it again,
    logged. Deleted again at once, it waits for the interval: a relay that keeps being refused must not hammer
    JetStream."""
    await ensure_stream(jetstream, STREAM)
    await jetstream.delete_stream(STREAM.name)
    await commit(database, outbox)
    with capture_logs() as logs:
        async with relaying(
            outbox.run(database, jetstream, streams=(STREAM,), pace=RelayPace(idle=0.05, retry=0.05))
        ):
            await eventually(partial(drained, database))
            assert await stored(jetstream) == 1

            await jetstream.delete_stream(STREAM.name)
            await commit(database, outbox)
            await eventually(partial(reached, registry, "ledger_outbox_publish_failed_total", 3))
            assert await pending(database) == 1

    assert STREAMS_ENSURED_EVERY == 30
    ensured = [entry for entry in logs if entry["event"] in STREAM_EVENTS]
    assert ensured == [{"event": "outbox.streams_ensured", "log_level": "warning", "streams": ["TEST"]}]


async def test_a_stream_the_relay_cannot_create_again_is_logged_and_the_events_wait(
    database: Database,
    jetstream: JetStreamContext,
    outbox: Outbox,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Here another stream holds its subjects now: JetStream refuses, and the relay goes on relaying."""
    await ensure_stream(jetstream, StreamSpec(name="OTHER", subjects=("taken.>",)))
    declared = StreamSpec(name="TEST", subjects=("taken.>",))
    await commit(database, outbox, subject="quiet.created")
    with capture_logs() as logs:
        async with relaying(
            outbox.run(database, jetstream, streams=(declared,), pace=RelayPace(idle=0.05, retry=0.05))
        ):
            await eventually(partial(reached, registry, "ledger_outbox_publish_failed_total", 2))

    failed = [entry for entry in logs if entry["event"] in STREAM_EVENTS]
    assert failed == [
        {"event": "outbox.streams_not_ensured", "log_level": "error", "exc_info": True, "streams": ["TEST"]}
    ]
    assert await pending(database) == 1


async def test_a_missing_stream_is_told_apart_from_other_failures(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
    registry: CollectorRegistry,
) -> None:
    """A subject someone listens on but nobody stores answers nothing, unlike one nobody listens on."""
    outbox = Outbox(tracing=tracing, registry=registry, publish_timeout=0.1)
    await silent(await connect(), "quiet.silent")
    await commit(database, outbox, subject="quiet.silent")
    with pytest.raises(PublishFailedError) as unacknowledged:
        await outbox.relay_once(database, jetstream)
    assert not unacknowledged.value.missing_stream

    async with database.transaction(None) as unit:
        await unit.connection.execute(delete(OutboxRecord))
    await commit(database, outbox, subject="quiet.created")
    await make_due(database)
    with pytest.raises(PublishFailedError) as nowhere:
        await outbox.relay_once(database, jetstream)
    assert nowhere.value.missing_stream


class RollbackFailedError(Exception):
    """What a clean-up raises when it fails: the cancellation it was cleaning up after becomes its context."""

    def __init__(self) -> None:
        super().__init__("the rollback failed")


def fail_rollback(connection: object) -> None:
    """SQLAlchemy's ``rollback`` event: the rollback of a transaction fails before it reaches PostgreSQL."""
    del connection
    raise RollbackFailedError


@asynccontextmanager
async def failing_rollbacks(database: Database) -> AsyncGenerator[None]:
    listen(database.engine.sync_engine, "rollback", fail_rollback)
    try:
        yield
    finally:
        remove(database.engine.sync_engine, "rollback", fail_rollback)


async def test_a_stop_that_a_clean_up_turned_into_another_error_ends_the_relay(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    outbox: Outbox,
    eventually: Eventually,
) -> None:
    """Stopped while it waits for JetStream, the relay rolls its pass back, and the rollback fails: the stop
    arrives as that error. The relay ends all the same, instead of logging a failed pass and going on."""
    await commit(database, outbox, subject="quiet.silent")
    await silent(await connect(), "quiet.silent")
    relay = asyncio.create_task(outbox.run(database, jetstream, pace=RelayPace(idle=MAX_IDLE)))
    try:
        await eventually(partial(unlocked, database, 0))  # the relay holds the event, waiting for an ack
        with capture_logs() as logs:
            async with failing_rollbacks(database):
                relay.cancel()
                await asyncio.wait((relay,), timeout=5)  # never cancels the relay again, unlike awaiting it
        assert relay.done(), "the relay went on after it was stopped"
        assert isinstance(relay.exception(), RollbackFailedError)
        assert logs == []
    finally:
        relay.cancel()
        await asyncio.wait((relay,), timeout=5)


async def waiting_for_a_lock(database: Database) -> bool:
    async with database.transaction(None) as unit:
        waiting = (
            select(func.count()).select_from(text("pg_locks")).where(column("granted", Boolean).is_(False))
        )
        return (await unit.connection.execute(waiting)).scalar_one() > 0


async def test_a_stop_that_a_clean_up_turned_into_another_error_is_no_unreadable_backlog(
    database_url: str, database: Database, outbox: Outbox, eventually: Eventually
) -> None:
    """Stopped while it waits to read the backlog: the error the clean-up raised goes on, not into a log line
    that would claim the backlog could not be read."""
    async with (
        Database.open(database_url, pool_size=1) as measuring_with,
        database.transaction(None) as holder,
    ):
        await holder.connection.execute(text("LOCK TABLE outbox IN ACCESS EXCLUSIVE MODE"))
        measuring = asyncio.create_task(outbox.measure_backlog(measuring_with))
        await eventually(partial(waiting_for_a_lock, database))
        with capture_logs() as logs:
            async with failing_rollbacks(measuring_with):
                measuring.cancel()
                with pytest.raises(RollbackFailedError):
                    await measuring
    assert logs == []
