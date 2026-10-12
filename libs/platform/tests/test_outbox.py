import asyncio
import contextlib
import traceback
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from math import inf, nan
from typing import Protocol
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from sqlalchemy import func, select
from structlog.testing import capture_logs
lazy from nats.aio.client import Client

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.outbox import (
    MAX_BATCH,
    MAX_RETRY_DELAY,
    InvalidOutboxOptionError,
    Outbox,
    OutboxRecord,
    PublishFailedError,
)
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_platform.tracing import Tracing
lazy from ledger_platform.database import Database

STREAM = StreamSpec(name="TEST", subjects=("test.>",))
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def event() -> Envelope:
    return Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=None)


@pytest.fixture
def tracing() -> Tracing:
    return Tracing(TracerProvider())


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def outbox(tracing: Tracing, registry: CollectorRegistry) -> Outbox:
    return Outbox(tracing=tracing, registry=registry, batch=10)


async def pending(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(OutboxRecord))).scalar_one()


async def stored(jetstream: JetStreamContext, stream: str = "TEST") -> int:
    return (await jetstream.stream_info(stream)).state.messages


async def drained(database: Database) -> bool:
    return await pending(database) == 0


async def unlocked(database: Database, count: int) -> bool:
    """Whether exactly ``count`` pending events are free of locks: what a pass skipping locked rows finds."""
    async with database.transaction(None) as unit:
        free = (
            await unit.connection.execute(select(OutboxRecord.id).with_for_update(skip_locked=True))
        ).all()
    return len(free) == count


async def delivered(jetstream: JetStreamContext, count: int) -> bool:
    return await stored(jetstream) >= count


async def eventually(check: Callable[[], Awaitable[bool]], *, within: float = 5) -> None:
    async with asyncio.timeout(within):
        while True:
            if await check():
                return
            await asyncio.sleep(0.02)


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
async def relaying(
    outbox: Outbox, database: Database, jetstream: JetStreamContext, *, idle: float = 60, retry: float = 1
) -> AsyncGenerator[None]:
    """The relay running in the background: cancelled and awaited on exit, so that nothing outlives the test.

    A relay left running would hold locks the fixtures' ``DROP TABLE`` waits for, and be destroyed pending.
    """
    relay = asyncio.create_task(outbox.run(database, jetstream, idle=idle, retry=retry))
    try:
        yield
    finally:
        relay.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay


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
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")
    async with relaying(outbox, database, jetstream, idle=0.05, retry=0.05):
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


async def test_the_relay_logs_a_failure_and_the_delay_before_the_next_attempt(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    waiting = asyncio.Event()

    async def stop_at_the_first_wait(delay: float) -> None:
        del delay
        waiting.set()
        await asyncio.Event().wait()

    outbox = Outbox(tracing=tracing, registry=registry, sleep=stop_at_the_first_wait)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")

    with capture_logs() as logs:
        async with relaying(outbox, database, jetstream, retry=2):  # no stream: the publish fails
            async with asyncio.timeout(5):
                await waiting.wait()

    assert logs == [{"event": "outbox.relay_failed", "log_level": "error", "exc_info": True, "retry_in": 2}]


async def test_the_relay_backs_off_exponentially_up_to_a_ceiling(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    delays: list[float] = []
    enough = asyncio.Event()

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 8:
            enough.set()
            await asyncio.Event().wait()  # the test ends the relay

    outbox = Outbox(tracing=tracing, registry=registry, sleep=sleep)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")

    async with relaying(outbox, database, jetstream, retry=1):  # no stream: every publish fails
        async with asyncio.timeout(10):
            await enough.wait()

    assert MAX_RETRY_DELAY == 30
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]


async def test_a_success_starts_the_delays_over(
    database: Database, jetstream: JetStreamContext, tracing: Tracing, registry: CollectorRegistry
) -> None:
    delays: list[float] = []
    failed_again = asyncio.Event()

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            await ensure_stream(jetstream, STREAM)  # NATS is fine from now on
        elif len(delays) == 4:
            failed_again.set()
            await asyncio.Event().wait()  # the test ends the relay

    outbox = Outbox(tracing=tracing, registry=registry, sleep=sleep)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.created")

    async with relaying(outbox, database, jetstream, retry=1):
        await eventually(partial(drained, database))  # three failures, then the event goes out
        await jetstream.delete_stream("TEST")
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
        async with asyncio.timeout(5):
            await failed_again.wait()

    assert delays == [1, 2, 4, 1]


async def test_a_commit_wakes_the_relay(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    await ensure_stream(jetstream, STREAM)
    async with relaying(outbox, database, jetstream, idle=60):
        await asyncio.sleep(0.1)
        async with database.transaction(None) as unit:
            await outbox.add(unit, event(), subject="test.created")
        await eventually(partial(delivered, jetstream, 1), within=2)


async def test_an_event_another_replica_wrote_is_found_within_idle(
    database: Database, jetstream: JetStreamContext, tracing: Tracing
) -> None:
    await ensure_stream(jetstream, STREAM)
    relay = Outbox(tracing=tracing, registry=CollectorRegistry())
    other_replica = Outbox(tracing=tracing, registry=CollectorRegistry())
    async with relaying(relay, database, jetstream, idle=0.05):
        await asyncio.sleep(0.1)
        async with database.transaction(None) as unit:
            await other_replica.add(
                unit, event(), subject="test.created"
            )  # wakes its own relay, not this one
        await eventually(partial(delivered, jetstream, 1), within=3)


async def test_more_events_than_a_batch_go_out_without_waiting(
    database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    await ensure_stream(jetstream, STREAM)
    async with database.transaction(None) as unit:
        for _ in range(25):
            await outbox.add(unit, event(), subject="test.created")
    async with relaying(outbox, database, jetstream, idle=60):
        await eventually(partial(delivered, jetstream, 25), within=3)


async def test_a_relay_does_not_wait_for_events_another_one_holds(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
) -> None:
    """The oldest five go to a subject nobody acknowledges: that relay holds them, waiting. Another replica's
    relay publishes the rest at once instead of queueing behind its locks."""
    await ensure_stream(jetstream, STREAM)
    await silent(await connect(), "quiet.created")
    stalled = Outbox(tracing=tracing, registry=CollectorRegistry(), batch=5, publish_timeout=30)
    other_replica = Outbox(tracing=tracing, registry=CollectorRegistry(), batch=5)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with database.transaction(None) as unit:
        for n in range(10):
            subject = "quiet.created" if n < 5 else "test.created"
            await stalled.add(unit, replace(event(), time=start + timedelta(seconds=n)), subject=subject)

    async with relaying(stalled, database, jetstream, idle=60):
        await eventually(partial(unlocked, database, 5))

        assert await other_replica.relay_once(database, jetstream) == 5

        assert await stored(jetstream) == 5
        assert await pending(database) == 5


async def test_stopping_the_relay_mid_publish_releases_the_events(
    database: Database,
    jetstream: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
    tracing: Tracing,
    registry: CollectorRegistry,
) -> None:
    outbox = Outbox(tracing=tracing, registry=registry, publish_timeout=30)
    async with database.transaction(None) as unit:
        await outbox.add(unit, event(), subject="test.silent")

    await silent(await connect(), "test.silent")
    async with relaying(outbox, database, jetstream, idle=60):
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


@pytest.mark.parametrize("timeout", [0, -1, nan, inf])
def test_a_publish_timeout_that_is_not_a_positive_number_is_refused(
    timeout: float, tracing: Tracing, registry: CollectorRegistry
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="publish_timeout"):
        Outbox(tracing=tracing, registry=registry, publish_timeout=timeout)


@pytest.mark.parametrize("idle", [0, -1, nan, inf])
async def test_an_idle_wait_that_is_not_a_positive_number_is_refused(
    idle: float, database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="idle"):
        await outbox.run(database, jetstream, idle=idle)


@pytest.mark.parametrize("retry", [0, -1, nan, inf, MAX_RETRY_DELAY + 1])
async def test_a_retry_delay_outside_its_bounds_is_refused(
    retry: float, database: Database, jetstream: JetStreamContext, outbox: Outbox
) -> None:
    with pytest.raises(InvalidOutboxOptionError, match="retry"):
        await outbox.run(database, jetstream, retry=retry)
