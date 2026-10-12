import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from math import inf, nan
from typing import ClassVar, Final, Protocol, TypedDict, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable
lazy from uuid import UUID

import pytest
from nats.errors import ConnectionClosedError
from nats.js.api import AckPolicy, ConsumerConfig
from nats.js.errors import APIError, BadRequestError, ServerError, ServiceUnavailableError
from nats.js.errors import NotFoundError as JetStreamNotFoundError
from opentelemetry.trace import StatusCode
from prometheus_client import CollectorRegistry
from pydantic import TypeAdapter
from sqlalchemy import (
    Boolean,
    Column,
    MetaData,
    Table,
    Text,
    Uuid,
    column,
    func,
    insert,
    literal_column,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs
lazy from nats.aio.client import Client
lazy from nats.aio.msg import Msg
lazy from nats.aio.subscription import Subscription
lazy from nats.js import JetStreamContext
lazy from nats.js.api import ConsumerInfo
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnavailableError,
)
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.consumer import (
    DEFAULT_ACK_WAIT,
    MAX_NAME_LENGTH,
    MIN_POLL,
    Consumer,
    ConsumerApi,
    ConsumerMetrics,
    ConsumerRefusedError,
    ConsumerSpec,
    InboxRecord,
    InvalidConsumerOptionError,
    Outcome,
    ensure_consumer,
)
from ledger_platform.database import DEFAULT_LIMITS, Database, DatabaseLimits
from ledger_platform.outbox import Outbox
from ledger_platform.streams import DEAD_LETTERS, StreamSpec, ensure_stream
lazy from ledger_platform.consumer import Handler
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.tracing import Tracing

NAME = "test-consumer"
STREAM = StreamSpec(name="EVENTS", subjects=("events.>",))
TENANT = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
EFFECTS = Table("effects", MetaData(), Column("event_id", Uuid(), nullable=False))
"""What handlers write in the event's transaction: it must happen once per event, whatever is delivered."""
COMPANY = literal_column("current_setting('ledger.company_id')", Text)
ADVISORY = TypeAdapter(dict[str, object])

type Eventually = Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]
"""The ``eventually`` fixture of conftest.py (basedpyright cannot resolve an import of a conftest)."""


class NeverValidError(InvalidInputError):
    code: ClassVar[str] = "never_valid"


class TemporarilyDownError(UnavailableError):
    code: ClassVar[str] = "temporarily_down"


class LeakyError(RuntimeError):
    """An error whose message holds what must not reach a span or a log's own fields."""

    def __init__(self) -> None:
        super().__init__("amount 31337 DOP")


@final
class Recorder:
    """A handler that records what it gets, writes an effect, and raises on its first ``failures`` calls."""

    def __init__(self, *, failures: int = 0, error: Callable[[], Exception] = RuntimeError) -> None:
        self.received: list[Envelope] = []
        self.tenants: list[str] = []
        self.calls = 0
        self._failures = failures
        self._error = error

    async def __call__(self, envelope: Envelope, unit: UnitOfWork) -> None:
        self.calls += 1
        await unit.connection.execute(insert(EFFECTS).values(event_id=envelope.id))
        if self.calls <= self._failures:
            raise self._error()
        self.received.append(envelope)
        self.tenants.append((await unit.connection.execute(select(COMPANY))).scalar_one())


@final
class Gated:
    """A handler that stays inside its transaction until the test opens the gate."""

    def __init__(self) -> None:
        self.calls = 0
        self.open = asyncio.Event()

    async def __call__(self, envelope: Envelope, unit: UnitOfWork) -> None:
        self.calls += 1
        await unit.connection.execute(insert(EFFECTS).values(event_id=envelope.id))
        await self.open.wait()


@final
class Swallower:
    """A handler whose first call makes a statement fail, catches the error and carries on."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, envelope: Envelope, unit: UnitOfWork) -> None:
        self.calls += 1
        await unit.connection.execute(insert(EFFECTS).values(event_id=envelope.id))
        if self.calls == 1:
            with contextlib.suppress(DBAPIError):
                await unit.connection.execute(text("SELECT 1 / 0"))


@final
class Counting(ConsumerApi):
    """JetStream, counting the consumers it was asked to write."""

    def __init__(self, api: ConsumerApi) -> None:
        self._api = api
        self.writes = 0

    @override
    async def consumer_info(self, stream: str, consumer: str) -> ConsumerInfo:
        return await self._api.consumer_info(stream, consumer)

    @override
    async def add_consumer(self, stream: str, config: ConsumerConfig) -> ConsumerInfo:
        self.writes += 1
        return await self._api.add_consumer(stream, config)


@final
class Answering(ConsumerApi):
    """JetStream that has no such consumer yet and answers its creation with ``error``."""

    def __init__(self, error: APIError) -> None:
        self._error = error

    @override
    async def consumer_info(self, stream: str, consumer: str) -> ConsumerInfo:
        raise JetStreamNotFoundError

    @override
    async def add_consumer(self, stream: str, config: ConsumerConfig) -> ConsumerInfo:
        raise self._error


@final
class Unserializable(ConsumerApi):
    """JetStream when nats-py cannot even build the request (it raises ``ValueError`` before sending)."""

    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    @override
    async def consumer_info(self, stream: str, consumer: str) -> ConsumerInfo:
        if not self._exists:
            raise JetStreamNotFoundError
        return ConsumerInfo(
            name=consumer,
            stream_name=stream,
            config=ConsumerConfig(name=consumer, durable_name=consumer, filter_subject="events.>"),
            created=datetime(2026, 1, 1, tzinfo=UTC),
        )

    @override
    async def add_consumer(self, stream: str, config: ConsumerConfig) -> ConsumerInfo:
        raise ValueError


class Subscribing(Protocol):
    """A NATS connection as ``settlement_of`` uses it: nats-py leaves the type of ``future`` open."""

    async def subscribe(self, subject: str) -> Subscription: ...


class Tuning(TypedDict, total=False):
    """What a test changes about the consumer's spec: the rest has the values the tests share."""

    name: str
    subject: str
    max_deliver: int
    backoff: tuple[float, ...]
    ack_wait: float


class ConsumerFactory(Protocol):
    def __call__(
        self, handler: Handler, *, database: Database | None = None, **tuning: Unpack[Tuning]
    ) -> Consumer: ...


def spec(handler: Handler, **tuning: Unpack[Tuning]) -> ConsumerSpec:
    return ConsumerSpec(
        name=tuning.get("name", NAME),
        stream="EVENTS",
        subject=tuning.get("subject", "events.>"),
        handler=handler,
        max_deliver=tuning.get("max_deliver", 10),
        backoff=tuning.get("backoff", (0.05,)),
        ack_wait=tuning.get("ack_wait", 30.0),
    )


@pytest.fixture
def metrics(registry: CollectorRegistry) -> ConsumerMetrics:
    return ConsumerMetrics(registry)


@pytest.fixture
async def streams(jetstream: JetStreamContext) -> JetStreamContext:
    await ensure_stream(jetstream, STREAM)
    await ensure_stream(jetstream, DEAD_LETTERS)
    return jetstream


@pytest.fixture
async def effects(database: Database) -> AsyncGenerator[None]:
    async with database.engine.begin() as connection:
        await connection.run_sync(EFFECTS.create)
    yield
    async with database.engine.begin() as connection:
        await connection.run_sync(EFFECTS.drop)


@pytest.fixture
def make(database: Database, tracing: Tracing, metrics: ConsumerMetrics, effects: None) -> ConsumerFactory:
    """Consumers of the tests' stream, on the fixtures' database unless a test gives another."""
    del effects  # the table the handlers write to
    fixtures = database

    def build(handler: Handler, *, database: Database | None = None, **tuning: Unpack[Tuning]) -> Consumer:
        return Consumer(
            spec(handler, **tuning),
            database=fixtures if database is None else database,
            tracing=tracing,
            metrics=metrics,
        )

    return build


def money(tenant: Tenant | None = TENANT) -> Envelope:
    return Envelope.wrap(Money(amount="1", currency="DOP"), source="test", tenant=tenant)


async def publish(jetstream: JetStreamContext, envelope: Envelope, subject: str = "events.created") -> None:
    await jetstream.publish(subject, envelope.data, headers=envelope.headers())


async def fetch(jetstream: JetStreamContext, count: int = 1) -> list[Msg]:
    """Deliveries for a test to hand to ``Consumer.handle`` itself."""
    subscription = await jetstream.pull_subscribe("events.>", durable="inspector", stream="EVENTS")
    return await subscription.fetch(count, timeout=5)


@asynccontextmanager
async def running(
    worker: Consumer, jetstream: JetStreamContext, *, poll: float = 0.2
) -> AsyncGenerator[None]:
    """The consumer in the background, cancelled and awaited on exit: none outlives a test.

    One left running would hold locks the fixtures' ``DROP TABLE`` waits for, and be destroyed pending.
    """
    task = asyncio.create_task(worker.run(jetstream, poll=poll))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def outcome(registry: CollectorRegistry, name: str, consumer: str = NAME) -> float | None:
    return registry.get_sample_value("ledger_events_total", {"consumer": consumer, "outcome": name})


async def received(recorder: Recorder, count: int) -> bool:
    return len(recorder.received) >= count


async def handling(gated: Gated) -> bool:
    return gated.calls >= 1


async def counted(registry: CollectorRegistry, name: str, count: float, consumer: str = NAME) -> bool:
    return (outcome(registry, name, consumer) or 0) >= count


async def dead_letters(jetstream: JetStreamContext) -> int:
    return (await jetstream.stream_info("DEAD_LETTERS")).state.messages


async def dead(jetstream: JetStreamContext, count: int) -> bool:
    return await dead_letters(jetstream) >= count


async def advisories(jetstream: JetStreamContext, count: int) -> list[dict[str, object]]:
    """What ``DEAD_LETTERS`` holds: JetStream's own notices, which name the stream and sequence."""
    subscription = await jetstream.pull_subscribe("$JS.EVENT.ADVISORY.>", stream="DEAD_LETTERS")
    return [ADVISORY.validate_json(message.data) for message in await subscription.fetch(count, timeout=5)]


async def bound(jetstream: JetStreamContext) -> bool:
    """Whether the consumer exists in JetStream, which it does once it has started."""
    try:
        await jetstream.consumer_info("EVENTS", NAME)
    except JetStreamNotFoundError:
        return False
    return True


async def acknowledged(jetstream: JetStreamContext, sequence: int) -> bool:
    """Whether the consumer has acknowledged every message up to ``sequence`` of the stream."""
    try:
        info = await jetstream.consumer_info("EVENTS", NAME)
    except JetStreamNotFoundError:  # the consumer is created when it starts
        return False
    return info.ack_floor is not None and info.ack_floor.stream_seq >= sequence


async def effects_written(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(EFFECTS))).scalar_one()


async def inbox(database: Database) -> list[tuple[str, UUID]]:
    async with database.transaction(None) as unit:
        rows = await unit.connection.execute(select(InboxRecord.consumer, InboxRecord.event_id))
        return [(consumer, event_id) for consumer, event_id in rows]


async def waiting_for_a_lock(database: Database) -> bool:
    async with database.transaction(None) as unit:
        waiting = (
            select(func.count()).select_from(text("pg_locks")).where(column("granted", Boolean).is_(False))
        )
        return (await unit.connection.execute(waiting)).scalar_one() > 0


async def listen(connection: Subscribing, subject: str) -> Subscription:
    return await connection.subscribe(subject)


async def settlement_of(client: Client, worker: Consumer, envelope: Envelope, *, delivery: int) -> bytes:
    """What the consumer tells JetStream about a delivery: the payload published to the message's reply."""
    reply = f"$JS.ACK.EVENTS.{NAME}.{delivery}.1.1.1700000000000000000.0"
    settled = await listen(client, reply)
    message = Msg(
        _client=client,
        subject="events.created",
        reply=reply,
        data=envelope.data,
        headers=envelope.headers(),
    )
    await worker.handle(message)
    return (await settled.next_msg(timeout=2)).data


@pytest.mark.parametrize("tenant", [TENANT, None], ids=["tenant", "no tenant"])
async def test_an_event_is_handled_in_the_transaction_of_its_tenant(
    tenant: Tenant | None,
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    eventually: Eventually,
) -> None:
    recorder = Recorder()
    envelope = money(tenant)
    await publish(streams, envelope)
    async with running(make(recorder), streams):
        await eventually(partial(received, recorder, 1))
        await eventually(partial(acknowledged, streams, 1))

    assert recorder.received == [envelope]
    assert recorder.tenants == ["" if tenant is None else str(tenant.company)]
    assert await inbox(database) == [(NAME, envelope.id)]


async def test_a_redelivered_event_is_handled_once(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """The same event stored twice by JetStream (a publish after the de-duplication window)."""
    recorder = Recorder()
    envelope = money()
    await publish(streams, envelope)
    await streams.publish(
        "events.created", envelope.data, headers=envelope.headers() | {"Nats-Msg-Id": "replay"}
    )
    async with running(make(recorder), streams):
        await eventually(partial(acknowledged, streams, 2))

    assert recorder.received == [envelope]
    assert (outcome(registry, "handled"), outcome(registry, "duplicate")) == (1, 1)
    assert await effects_written(database) == 1
    assert await inbox(database) == [(NAME, envelope.id)]


async def test_a_failing_handler_is_retried_until_it_succeeds_and_leaves_one_effect(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Each failed attempt wrote its effect and the claim, and both were rolled back with it."""
    recorder = Recorder(failures=2)
    await publish(streams, money())
    async with running(make(recorder), streams):
        await eventually(partial(received, recorder, 1))
        await eventually(partial(acknowledged, streams, 1))

    assert recorder.calls == 3
    assert (outcome(registry, "failed"), outcome(registry, "handled")) == (2, 1)
    assert await effects_written(database) == 1
    assert len(await inbox(database)) == 1


@pytest.mark.parametrize(
    "error",
    [NotFoundError, ConflictError, ForbiddenError, UnavailableError, TemporarilyDownError, LedgerError],
)
async def test_a_handler_error_that_may_pass_is_retried_and_then_handled(
    error: Callable[[], Exception],
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Only an error of kind ``INVALID`` says the event can never succeed."""
    recorder = Recorder(failures=1, error=error)
    await publish(streams, money())
    async with running(make(recorder), streams):
        await eventually(partial(received, recorder, 1))

    assert recorder.calls == 2
    assert (outcome(registry, "failed"), outcome(registry, "rejected")) == (1, 0)
    assert await dead_letters(streams) == 0


async def test_a_failing_event_does_not_hold_back_the_ones_behind_it(
    streams: JetStreamContext, make: ConsumerFactory, registry: CollectorRegistry, eventually: Eventually
) -> None:
    recorder = Recorder(failures=1)
    events = [money() for _ in range(3)]
    for envelope in events:
        await publish(streams, envelope)
    async with running(make(recorder), streams):
        await eventually(partial(acknowledged, streams, 3))

    assert sorted(envelope.id for envelope in recorder.received) == sorted(envelope.id for envelope in events)
    assert (outcome(registry, "handled"), outcome(registry, "failed")) == (3, 1)


async def test_an_event_that_keeps_failing_ends_in_dead_letters(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    recorder = Recorder(failures=1000)
    await publish(streams, money())
    async with running(make(recorder, max_deliver=2), streams):
        await eventually(partial(dead, streams, 1))

    (advisory,) = await advisories(streams, 1)
    assert advisory["type"] == "io.nats.jetstream.advisory.v1.max_deliver"
    assert (advisory["stream"], advisory["consumer"], advisory["stream_seq"]) == ("EVENTS", NAME, 1)
    assert (recorder.calls, outcome(registry, "failed")) == (2, 2)
    assert await effects_written(database) == 0
    assert await inbox(database) == []


async def test_a_permanently_invalid_event_goes_to_dead_letters_at_once(
    streams: JetStreamContext, make: ConsumerFactory, registry: CollectorRegistry, eventually: Eventually
) -> None:
    recorder = Recorder(failures=1000, error=NeverValidError)
    await publish(streams, money())
    async with running(make(recorder), streams):
        await eventually(partial(dead, streams, 1))

    (advisory,) = await advisories(streams, 1)
    assert advisory["type"] == "io.nats.jetstream.advisory.v1.terminated"
    assert (advisory["stream"], advisory["consumer"], advisory["stream_seq"]) == ("EVENTS", NAME, 1)
    assert (recorder.calls, outcome(registry, "rejected"), outcome(registry, "failed")) == (1, 1, 0)


@pytest.mark.parametrize(
    "headers", [None, {"ce-id": "not-an-id", "Nats-Msg-Id": "broken"}], ids=["no headers", "invalid headers"]
)
async def test_an_unreadable_message_goes_to_dead_letters_without_reaching_the_handler(
    headers: dict[str, str] | None,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """And the consumer carries on: the next event is handled."""
    recorder = Recorder()
    good = money()
    broken = None if headers is None else good.headers() | headers
    await streams.publish("events.created", b"not an event", headers=broken)
    await publish(streams, good)
    async with running(make(recorder), streams):
        await eventually(partial(counted, registry, "handled", 1))
        await eventually(partial(dead, streams, 1))

    (advisory,) = await advisories(streams, 1)
    assert advisory["type"] == "io.nats.jetstream.advisory.v1.terminated"
    assert advisory["stream_seq"] == 1
    assert (recorder.calls, recorder.received) == (1, [good])
    assert (outcome(registry, "malformed"), outcome(registry, "handled")) == (1, 1)


async def test_the_producer_s_trace_continues_in_the_consumer(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    tracing: Tracing,
    exporter: InMemorySpanExporter,
) -> None:
    outbox = Outbox(tracing=tracing, registry=CollectorRegistry())
    with tracing.tracer.start_as_current_span("request"):
        async with database.transaction(TENANT) as unit:
            await outbox.add(unit, money(), subject="events.created")
    await outbox.relay_once(database, streams)
    (message,) = await fetch(streams)
    await make(Recorder()).handle(message)

    spans = {span.name: span for span in exporter.get_finished_spans()}
    request, consumed = spans["request"], spans[f"consume {NAME}"]
    assert request.context is not None
    assert consumed.context is not None
    assert consumed.context.trace_id == request.context.trace_id


async def test_a_failed_handling_shows_in_its_span_by_error_type_alone(
    streams: JetStreamContext, make: ConsumerFactory, exporter: InMemorySpanExporter
) -> None:
    await publish(streams, money())
    (message,) = await fetch(streams)
    await make(Recorder(failures=1, error=LeakyError)).handle(message)

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [(event.name, dict(event.attributes or {})) for event in span.events] == [
        ("exception", {"exception.type": f"{LeakyError.__module__}.LeakyError"})
    ]
    assert "31337" not in span.to_json()


async def test_a_failing_handler_is_logged_with_the_event_and_nothing_of_its_content(
    streams: JetStreamContext, make: ConsumerFactory
) -> None:
    """The record's own fields. The traceback of the handler's error says whatever that error says."""
    envelope = Envelope.wrap(Money(amount="31337", currency="DOP"), source="test", tenant=TENANT)
    await publish(streams, envelope)
    (message,) = await fetch(streams)
    with capture_logs() as logs:
        await make(Recorder(failures=1)).handle(message)

    assert logs == [
        {
            "event": "consumer.handler_failed",
            "log_level": "error",
            "exc_info": True,
            "consumer": NAME,
            "event_id": str(envelope.id),
            "type": "ledger.common.v1.Money",
            "delivery": 1,
            "stream_sequence": 1,
        }
    ]
    for content in ("31337", "DOP", str(TENANT.organization), str(TENANT.company)):
        assert content not in repr(logs)


async def test_a_rejected_event_is_logged_with_the_stable_code_of_the_error(
    streams: JetStreamContext, make: ConsumerFactory
) -> None:
    envelope = money()
    await publish(streams, envelope)
    (message,) = await fetch(streams)
    with capture_logs() as logs:
        await make(Recorder(failures=1, error=NeverValidError)).handle(message)

    assert logs == [
        {
            "event": "consumer.event_rejected",
            "log_level": "error",
            "exc_info": True,
            "consumer": NAME,
            "event_id": str(envelope.id),
            "type": "ledger.common.v1.Money",
            "delivery": 1,
            "stream_sequence": 1,
            "code": "never_valid",
        }
    ]


@pytest.mark.parametrize("field", ["ce-id", "ce-time", "ce-source"])
async def test_an_unreadable_message_is_logged_with_the_failing_field_but_not_its_value_or_body(
    field: str, streams: JetStreamContext, make: ConsumerFactory
) -> None:
    headers = money().headers() | {field: "secret header"}
    await streams.publish("events.created", b"secret body", headers=headers)
    (message,) = await fetch(streams)
    with capture_logs() as logs:
        await make(Recorder()).handle(message)

    assert logs == [
        {
            "event": "consumer.event_malformed",
            "log_level": "error",
            "consumer": NAME,
            "subject": "events.created",
            "field": field,
            "delivery": 1,
            "stream_sequence": 1,
        }
    ]


async def test_a_message_without_headers_is_logged_with_the_first_header_it_lacks(
    streams: JetStreamContext, make: ConsumerFactory
) -> None:
    await streams.publish("events.created", b"secret body")
    (message,) = await fetch(streams)
    with capture_logs() as logs:
        await make(Recorder()).handle(message)

    assert logs == [
        {
            "event": "consumer.event_malformed",
            "log_level": "error",
            "consumer": NAME,
            "subject": "events.created",
            "field": "ce-specversion",
            "delivery": 1,
            "stream_sequence": 1,
        }
    ]


async def test_a_claim_that_cannot_be_made_is_logged_as_such_and_never_reaches_the_handler(
    database: Database, impatient: Database, streams: JetStreamContext, make: ConsumerFactory
) -> None:
    """Another transaction holds the event's inbox row: PostgreSQL gives up on the claim after 100 ms."""
    envelope = money()
    await publish(streams, envelope)
    (message,) = await fetch(streams)
    recorder = Recorder()
    async with database.transaction(None) as holder:
        await holder.connection.execute(
            insert(InboxRecord).values(consumer=NAME, event_id=envelope.id, processed_at=datetime.now(UTC))
        )
        with capture_logs() as logs:
            await make(recorder, database=impatient).handle(message)

    assert recorder.calls == 0
    assert logs == [
        {
            "event": "consumer.claim_failed",
            "log_level": "error",
            "exc_info": True,
            "consumer": NAME,
            "event_id": str(envelope.id),
            "type": "ledger.common.v1.Money",
            "delivery": 1,
            "stream_sequence": 1,
        }
    ]


async def test_a_handler_that_swallows_a_database_error_is_not_acknowledged_but_retried(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """A statement failed and the handler went on: the commit would be a rollback, so no acknowledgement."""
    swallower = Swallower()
    await publish(streams, money())
    async with running(make(swallower), streams):
        await eventually(partial(acknowledged, streams, 1))

    assert swallower.calls == 2
    assert (outcome(registry, "failed"), outcome(registry, "handled")) == (1, 1)
    assert await effects_written(database) == 1
    assert len(await inbox(database)) == 1


async def test_consumers_of_one_service_share_a_registry_and_are_counted_apart(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    alpha, beta = Recorder(), Recorder()
    envelope = money()
    await publish(streams, envelope)
    async with running(make(alpha, name="alpha"), streams), running(make(beta, name="beta"), streams):
        await eventually(partial(counted, registry, "handled", 1, "alpha"))
        await eventually(partial(counted, registry, "handled", 1, "beta"))

    assert (outcome(registry, "handled", "alpha"), outcome(registry, "handled", "beta")) == (1, 1)
    assert sorted(await inbox(database)) == [("alpha", envelope.id), ("beta", envelope.id)]


async def test_every_outcome_of_a_consumer_is_exported_from_the_start(
    make: ConsumerFactory, registry: CollectorRegistry
) -> None:
    """A counter that appears with its first event hides that event from ``rate()`` and ``increase()``."""
    make(Recorder())
    assert {name: outcome(registry, name) for name in Outcome} == dict.fromkeys(Outcome, 0)


async def test_a_restarted_consumer_takes_up_where_the_last_one_stopped(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    before, after = Recorder(), Recorder()
    first, second = money(), money()
    await publish(streams, first)
    async with running(make(before), streams):
        await eventually(partial(acknowledged, streams, 1))
    await publish(streams, second)
    async with running(make(after), streams):
        await eventually(partial(received, after, 1))

    assert (before.received, after.received) == ([first], [second])


async def test_an_idle_consumer_keeps_polling(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    recorder = Recorder()
    async with running(make(recorder), streams, poll=0.1):
        await asyncio.sleep(0.5)  # several polls that find nothing
        await publish(streams, money())
        await eventually(partial(received, recorder, 1))


async def test_a_changed_max_deliver_ack_wait_and_backoff_take_effect_on_the_existing_consumer(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    """A new version of the service changes what its consumer does about failures: all of it applies."""
    async with running(make(Recorder(), max_deliver=3, backoff=(60.0,), ack_wait=7.0), streams):
        await eventually(partial(bound, streams))
    created = (await streams.consumer_info("EVENTS", NAME)).config
    assert (created.durable_name, created.ack_policy, created.filter_subject) == (
        NAME,
        "explicit",
        "events.>",
    )
    assert (created.max_deliver, created.ack_wait) == (3, 7.0)

    recorder = Recorder(failures=1)
    await publish(streams, money())
    async with running(make(recorder, max_deliver=5, backoff=(0.05,), ack_wait=9.0), streams):
        await eventually(partial(received, recorder, 1))  # the failed delivery was back after 0.05 s, not 60
    updated = (await streams.consumer_info("EVENTS", NAME)).config
    assert (updated.max_deliver, updated.ack_wait) == (5, 9.0)


async def test_updating_the_other_fields_leaves_the_pending_messages_alone(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    """A message is being handled when the consumer is updated: JetStream keeps its state, and the message."""
    gated = Gated()
    await publish(streams, money())
    async with running(make(gated), streams):
        await eventually(partial(handling, gated))

        await ensure_consumer(streams, spec(Recorder(), max_deliver=4, ack_wait=40.0))

        info = await streams.consumer_info("EVENTS", NAME)
        assert (info.config.max_deliver, info.config.ack_wait) == (4, 40.0)
        assert info.num_ack_pending == 1
        gated.open.set()
        await eventually(partial(acknowledged, streams, 1))
    assert gated.calls == 1


async def test_a_changed_subject_is_refused_and_the_existing_consumer_is_left_alone(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    """One durable consumer serves every replica: widening its subject in place would hand the events of a new
    kind to replicas that cannot read them, and narrowing it (an old replica restarting) would skip them."""
    async with running(make(Recorder(), subject="events.created", max_deliver=3), streams):
        await eventually(partial(bound, streams))
    before = (await streams.consumer_info("EVENTS", NAME)).config

    async with asyncio.timeout(5):  # a consumer that went on would run for ever
        with pytest.raises(ConsumerRefusedError) as refused:
            await make(Recorder(), subject="events.>", max_deliver=5, ack_wait=9.0).run(streams)

    assert (refused.value.consumer, refused.value.stream, refused.value.fields) == (
        NAME,
        "EVENTS",
        ("filter_subject",),
    )
    assert f"a new name, such as '{NAME}-v2'" in str(refused.value)
    assert (await streams.consumer_info("EVENTS", NAME)).config == before  # not even the other fields


async def test_a_new_name_with_the_new_subject_is_a_consumer_of_its_own_that_starts_with_the_stream(
    streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    first, second = money(), money()
    await publish(streams, first, "events.created")
    created_only = Recorder()
    async with running(make(created_only, subject="events.created"), streams):
        await eventually(partial(received, created_only, 1))
    await publish(streams, second, "events.deleted")

    everything = Recorder()
    async with running(make(everything, name=f"{NAME}-v2", subject="events.>"), streams):
        await eventually(partial(received, everything, 2))

    assert sorted(envelope.id for envelope in everything.received) == sorted([first.id, second.id])
    assert (await streams.consumer_info("EVENTS", NAME)).config.filter_subject == "events.created"
    assert (await streams.consumer_info("EVENTS", f"{NAME}-v2")).config.filter_subject == "events.>"


async def test_a_consumer_whose_ack_policy_would_change_is_refused_and_left_alone(
    streams: JetStreamContext,
) -> None:
    """Someone created it with another ack policy, which JetStream never lets anyone change: the chassis
    refuses without asking (JetStream's own answer, 10012, is a wrapper that also carries passing trouble)."""
    config = ConsumerConfig(
        name=NAME, durable_name=NAME, ack_policy=AckPolicy.NONE, filter_subject="events.>"
    )
    await Counting(streams).add_consumer("EVENTS", config)
    counting = Counting(streams)

    with pytest.raises(ConsumerRefusedError) as refused:
        await ensure_consumer(counting, spec(Recorder()))

    assert (refused.value.consumer, refused.value.stream, refused.value.fields) == (
        NAME,
        "EVENTS",
        ("ack_policy",),
    )
    assert str(refused.value) == (
        f"consumer '{NAME}' of stream 'EVENTS' cannot change ack_policy: JetStream never changes the ack"
        f" policy of a consumer: give the consumer a new name, such as '{NAME}-v2'"
    )
    assert refused.value.__cause__ is None
    assert counting.writes == 0
    assert (await streams.consumer_info("EVENTS", NAME)).config.ack_policy == "none"


async def test_a_consumer_whose_subject_and_ack_policy_would_change_names_both(
    streams: JetStreamContext,
) -> None:
    config = ConsumerConfig(name=NAME, durable_name=NAME, ack_policy=AckPolicy.ALL, filter_subject="events.a")
    await Counting(streams).add_consumer("EVENTS", config)
    with pytest.raises(ConsumerRefusedError) as refused:
        await ensure_consumer(streams, spec(Recorder()))
    assert refused.value.fields == ("ack_policy", "filter_subject")


async def test_a_new_consumer_is_created_as_the_spec_says(streams: JetStreamContext) -> None:
    api = Counting(streams)
    await ensure_consumer(api, spec(Recorder(), subject="events.created", max_deliver=3, ack_wait=7.0))

    config = (await streams.consumer_info("EVENTS", NAME)).config
    assert (config.durable_name, config.filter_subject, config.ack_policy) == (
        NAME,
        "events.created",
        "explicit",
    )
    assert (config.max_deliver, config.ack_wait) == (3, 7.0)
    assert api.writes == 1


async def test_a_consumer_that_is_as_the_spec_says_is_not_written_again(streams: JetStreamContext) -> None:
    api = Counting(streams)
    await ensure_consumer(api, spec(Recorder()))
    await ensure_consumer(api, spec(Recorder()))
    assert api.writes == 1

    await ensure_consumer(api, spec(Recorder(), ack_wait=9.0))
    assert api.writes == 2


async def test_a_consumer_of_a_stream_that_does_not_exist_yet_says_so_and_binds_once_it_does(
    jetstream: JetStreamContext,
) -> None:
    """No configuration mistake: whoever publishes to the stream creates it when it starts, maybe later. The
    error goes on as it came, for the supervisor to try again."""
    with capture_logs() as logs, pytest.raises(JetStreamNotFoundError) as missing:
        await ensure_consumer(jetstream, spec(Recorder()))
    assert missing.value.err_code == 10059
    assert logs == [
        {"event": "consumer.stream_missing", "log_level": "warning", "consumer": NAME, "stream": "EVENTS"}
    ]

    await ensure_stream(jetstream, STREAM)
    await ensure_consumer(jetstream, spec(Recorder()))
    assert await bound(jetstream)


@pytest.mark.parametrize(
    "api", [Unserializable(exists=True), Unserializable(exists=False)], ids=["on update", "on create"]
)
async def test_a_request_nats_py_cannot_build_is_a_refused_consumer_too(api: ConsumerApi) -> None:
    with pytest.raises(ConsumerRefusedError, match=f"'{NAME}'") as refused:
        await ensure_consumer(api, spec(Recorder(), ack_wait=9.0))
    assert isinstance(refused.value.__cause__, ValueError)


@pytest.mark.usefixtures("effects")
async def test_a_claim_is_dated_by_the_clock_of_the_consumer(
    database: Database, streams: JetStreamContext, tracing: Tracing, metrics: ConsumerMetrics
) -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    worker = Consumer(
        spec(Recorder()), database=database, tracing=tracing, metrics=metrics, clock=lambda: moment
    )
    await publish(streams, money())
    (message,) = await fetch(streams)
    await worker.handle(message)

    async with database.transaction(None) as unit:
        processed = (await unit.connection.execute(select(InboxRecord.processed_at))).scalar_one()
    assert processed == moment


async def test_a_closed_connection_ends_the_consumer_with_the_error_of_the_connection(
    connect: Callable[[], Awaitable[Client]],
    streams: JetStreamContext,
    make: ConsumerFactory,
    eventually: Eventually,
) -> None:
    """The consumer does not paper over NATS being gone: the service's supervision sees it end."""
    client = await connect()
    worker = asyncio.create_task(make(Recorder()).run(JetStreamContext(client), poll=0.1))
    try:
        await eventually(partial(bound, streams))
        await client.close()
        async with asyncio.timeout(5):
            with pytest.raises(ConnectionClosedError):
                await worker
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionClosedError):
            await worker


async def test_stopping_a_consumer_mid_handler_rolls_back_and_hands_the_whole_batch_to_the_next(
    database: Database, streams: JetStreamContext, make: ConsumerFactory, eventually: Eventually
) -> None:
    """Three events are in one batch and the first is inside its handler when the consumer is stopped.

    Nothing of it commits, and the next consumer does not wait for ``ack_wait`` (30 seconds here) for that
    event or for the two it never started.
    """
    events = [money() for _ in range(3)]
    for envelope in events:
        await publish(streams, envelope)
    gated = Gated()
    async with running(make(gated), streams):
        await eventually(partial(handling, gated))

    assert gated.calls == 1
    assert await effects_written(database) == 0
    assert await inbox(database) == []
    successor = Recorder()
    async with running(make(successor), streams):
        await eventually(partial(received, successor, 3))
    assert sorted(envelope.id for envelope in successor.received) == sorted(
        envelope.id for envelope in events
    )


async def test_a_delivery_that_arrives_while_the_first_is_still_being_handled_is_a_duplicate(
    database: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """The handler outlasts ``ack_wait``: JetStream delivers the event again to another replica.

    The second claim waits on the inbox's primary key for the first transaction to end, then finds the
    event handled: one effect, one duplicate.
    """
    gated = Gated()
    envelope = money()
    await publish(streams, envelope)
    async with running(make(gated, ack_wait=0.5), streams):
        await eventually(partial(handling, gated))
        async with running(make(gated, ack_wait=0.5), streams):
            await eventually(partial(waiting_for_a_lock, database))
            gated.open.set()
            await eventually(partial(acknowledged, streams, 1))
            await eventually(partial(counted, registry, "duplicate", 1))

    assert gated.calls == 1
    assert (outcome(registry, "handled"), outcome(registry, "duplicate")) == (1, 1)
    assert await effects_written(database) == 1
    assert await inbox(database) == [(NAME, envelope.id)]


@pytest.fixture
async def impatient(database_url: str, database: Database) -> AsyncGenerator[Database]:
    """The tests' database, reached by connections that give up waiting for a lock after 100 milliseconds."""
    del database  # its tables exist, and are dropped after this pool is closed
    limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=limits) as opened:
        yield opened


async def test_a_claim_that_waits_longer_than_the_lock_timeout_fails_and_the_event_is_still_handled_once(
    impatient: Database,
    streams: JetStreamContext,
    make: ConsumerFactory,
    registry: CollectorRegistry,
    eventually: Eventually,
) -> None:
    """Behind a handling that outlasts PostgreSQL's ``lock_timeout`` a claim is cancelled: that delivery is
    counted as failed and retried with the backoff, and the event is still handled once."""
    gated = Gated()
    await publish(streams, money())
    async with running(make(gated, ack_wait=0.5), streams):
        await eventually(partial(handling, gated))
        async with running(make(gated, ack_wait=0.5, database=impatient), streams):
            await eventually(partial(counted, registry, "failed", 1))
            gated.open.set()
            await eventually(partial(acknowledged, streams, 1))

    assert gated.calls == 1
    assert outcome(registry, "handled") == 1
    assert await effects_written(impatient) == 1
    assert await dead_letters(streams) == 0


@pytest.mark.parametrize(("delivery", "delay"), [(1, 0.1), (2, 0.5), (3, 2.0), (4, 2.0), (50, 2.0)])
def test_the_delay_after_a_failed_delivery_is_the_backoff_entry_and_then_its_last(
    delivery: int, delay: float
) -> None:
    assert spec(Recorder(), backoff=(0.1, 0.5, 2.0)).retry_delay(delivery) == delay


@pytest.mark.parametrize(("delivery", "delay"), [(1, 100_000_000), (3, 2_000_000_000), (9, 2_000_000_000)])
async def test_a_failed_delivery_is_nacked_with_the_delay_of_its_number(
    delivery: int,
    delay: int,
    connect: Callable[[], Awaitable[Client]],
    make: ConsumerFactory,
) -> None:
    worker = make(Recorder(failures=1), backoff=(0.1, 0.5, 2.0))
    payload = await settlement_of(await connect(), worker, money(), delivery=delivery)
    assert payload == b'-NAK {"delay": %d}' % delay


@pytest.mark.parametrize("name", ["a", "orders-projection", "Invoicing_v1", "x" * MAX_NAME_LENGTH])
def test_a_name_nats_accepts_is_taken(name: str) -> None:
    assert spec(Recorder(), name=name).name == name


@pytest.mark.parametrize(
    "name",
    ["", " ", "a b", "a.b", "a*", ">", "a/b", "a\\b", "a\tb", "a\nb", "ñ", "a\x00", "\N{NO-BREAK SPACE}"],
)
def test_a_name_nats_would_refuse_or_misread_is_refused_at_construction(name: str) -> None:
    with pytest.raises(InvalidConsumerOptionError, match="name"):
        spec(Recorder(), name=name)


def test_a_name_longer_than_nats_allows_is_refused_and_not_echoed_whole() -> None:
    assert MAX_NAME_LENGTH == 255
    with pytest.raises(InvalidConsumerOptionError, match="name") as refused:
        spec(Recorder(), name="x" * (MAX_NAME_LENGTH + 1))
    assert len(str(refused.value)) < 200


@pytest.mark.parametrize("max_deliver", [0, -1])
def test_a_consumer_that_may_not_deliver_is_refused(max_deliver: int) -> None:
    """JetStream reads ``-1`` as unlimited: an event that keeps failing would never reach dead letters."""
    with pytest.raises(InvalidConsumerOptionError, match="max_deliver"):
        spec(Recorder(), max_deliver=max_deliver)


@pytest.mark.parametrize("backoff", [(), (0.0,), (-1.0,), (nan,), (inf,), (1.0, 0.0), (1.0, nan)], ids=repr)
def test_a_backoff_that_is_not_a_list_of_positive_finite_delays_is_refused(
    backoff: tuple[float, ...],
) -> None:
    with pytest.raises(InvalidConsumerOptionError, match="backoff"):
        spec(Recorder(), backoff=backoff)


def test_a_backoff_longer_than_the_deliveries_is_refused() -> None:
    with pytest.raises(InvalidConsumerOptionError, match="backoff"):
        spec(Recorder(), max_deliver=2, backoff=(1.0, 2.0, 3.0))


def test_a_backoff_as_long_as_the_deliveries_is_taken() -> None:
    assert spec(Recorder(), max_deliver=2, backoff=(1.0, 2.0)).retry_delay(2) == 2.0


def test_the_default_ack_wait_outlasts_a_claim_and_a_statement_of_the_default_database_limits() -> None:
    """A claim may wait ``lock_timeout`` and a statement may take ``statement_timeout``: and then some."""
    claim_and_statement = DEFAULT_LIMITS.lock_timeout + DEFAULT_LIMITS.statement_timeout
    assert 2 * claim_and_statement.total_seconds() <= DEFAULT_ACK_WAIT
    bare = ConsumerSpec(name="x", stream="EVENTS", subject="events.>", handler=Recorder())
    assert bare.ack_wait == DEFAULT_ACK_WAIT


@pytest.mark.parametrize("ack_wait", [0, -1, nan, inf])
def test_an_ack_wait_that_is_not_a_positive_finite_number_is_refused(ack_wait: float) -> None:
    with pytest.raises(InvalidConsumerOptionError, match="ack_wait"):
        spec(Recorder(), ack_wait=ack_wait)


@pytest.mark.parametrize("batch", [0, -1])
async def test_a_batch_below_one_is_refused(
    batch: int, streams: JetStreamContext, make: ConsumerFactory
) -> None:
    async with asyncio.timeout(5):  # a consumer that accepted the value would run for ever
        with pytest.raises(InvalidConsumerOptionError, match="batch"):
            await make(Recorder()).run(streams, batch=batch)


@pytest.mark.parametrize("poll", [0, -1, nan, inf, MIN_POLL / 2])
async def test_a_poll_that_is_not_a_positive_finite_number_of_seconds_is_refused(
    poll: float, streams: JetStreamContext, make: ConsumerFactory
) -> None:
    async with asyncio.timeout(5):  # a consumer that accepted the value would run for ever
        with pytest.raises(InvalidConsumerOptionError, match="poll"):
            await make(Recorder()).run(streams, poll=poll)


TRANSIENT: Final = {
    "no responders": ServiceUnavailableError(),
    "10008 temporarily unavailable": ServiceUnavailableError(code=503, err_code=10008),
    "10004 incomplete results": ServiceUnavailableError(code=503, err_code=10004),
    "10023 insufficient resources": ServiceUnavailableError(code=503, err_code=10023),
    "10009 no leader": ServerError(code=500, err_code=10009),
    "10007 not assigned": ServerError(code=500, err_code=10007),
    "10118 stream offline": ServerError(code=500, err_code=10118),
    "10119 consumer offline": ServerError(code=500, err_code=10119),
    "10005 no suitable peers": BadRequestError(code=400, err_code=10005),
    "10202 member change in progress": BadRequestError(code=400, err_code=10202),
    "10012 create failed": ServerError(code=500, err_code=10012),
    "10059 stream not found": JetStreamNotFoundError(code=404, err_code=10059),
}
"""What JetStream answers while it cannot serve for now (nats-server 2.12.15, server/errors.json). 10012 wraps
whatever went wrong, passing trouble included; 10059 is a stream whose service has not started yet."""
REFUSALS: Final = {
    "10013 name in use": BadRequestError(
        code=400, err_code=10013, description="consumer name already in use"
    ),
    "10002 account resources": BadRequestError(code=400, err_code=10002, description="resource limits"),
    "10003 bad request": BadRequestError(code=400, err_code=10003, description="bad request"),
    "10026 max consumers": BadRequestError(code=400, err_code=10026, description="maximum consumers"),
    "10116 max deliver": BadRequestError(code=400, err_code=10116, description="max deliver"),
    "10131 filter mismatch": BadRequestError(code=400, err_code=10131, description="filter mismatch"),
    "10132 name mismatch": BadRequestError(code=400, err_code=10132, description="name mismatch"),
    "10148 already exists": BadRequestError(code=400, err_code=10148, description="already exists"),
    "10149 does not exist": BadRequestError(code=400, err_code=10149, description="does not exist"),
}
"""What JetStream answers to a consumer it will never accept as configured (same source)."""


@pytest.mark.parametrize("answer", TRANSIENT.values(), ids=TRANSIENT.keys())
async def test_a_jetstream_that_cannot_serve_for_now_is_no_refusal_and_its_error_goes_on(
    answer: APIError,
) -> None:
    """A blip during a deploy must not look like a misconfiguration: the error reaches the supervisor as it
    came, and the supervisor starts the consumer again later."""
    with pytest.raises(APIError) as raised:
        await ensure_consumer(Answering(answer), spec(Recorder()))
    assert raised.value is answer


@pytest.mark.parametrize("answer", REFUSALS.values(), ids=REFUSALS.keys())
async def test_a_configuration_jetstream_will_never_accept_is_refused_with_its_answer(
    answer: APIError,
) -> None:
    with pytest.raises(ConsumerRefusedError) as refused:
        await ensure_consumer(Answering(answer), spec(Recorder()))
    assert refused.value.__cause__ is answer
    assert (
        str(refused.value) == f"consumer '{NAME}' of stream 'EVENTS' cannot be created: {answer.description}"
    )
