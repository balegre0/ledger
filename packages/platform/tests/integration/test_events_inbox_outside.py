"""Outside consumers on PostgreSQL (§3.5, rule 13): a handler with no unit open, its row deleted by its lease.

The handler may call a facade (it holds no transaction); while it runs, its row's lease is renewed under its
fencing token; then the row is deleted, or its failure recorded, in a unit of its own that names the token. A
runner whose lease was taken over records nothing. Rows are made due and leases taken over by moving their
columns as the superuser, never by a clock: due and lease comparisons are the database's (``now()``).
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import psycopg
import pytest
from opentelemetry.trace import StatusCode
from prometheus_client import CollectorRegistry
from pydantic import TypeAdapter
from sqlalchemy import text
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ledger_kernel.errors import InvalidInputError, UnavailableError
from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import ConsumerDecl
from ledger_platform.data.audit import Actor, current_actor
from ledger_platform.data.unit_of_work import open_unit
from ledger_platform.events.consumers import Consumer, ConsumerSpec
from ledger_platform.events.dispatcher import Dispatcher
from ledger_platform.events.inbox import InboxRunner
from ledger_platform.runtime.calls import SystemCall, acting
from ledger_platform.runtime.facade import UnitOpenError, facade
from ledger_testing import waiting
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events import RawEvent
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.postgres import WorkerDatabase

type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]

TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
NAME: Final = "other.renames"
ACTOR: Final = f"system:consumer:{NAME}"
RENAMED: Final = "probe.thing.renamed"
STATE: Final = "SELECT status, attempts, last_error_code FROM other.inbox WHERE event_id = %s"
EXPIRE: Final = "UPDATE other.inbox SET lease_until = now() - interval '1 hour'"
TAKE_OVER: Final = (
    "UPDATE other.inbox SET lease_token = gen_random_uuid(), lease_until = now() + interval '1 minute'"
)
REFUSE_RENEWALS: Final = """
    CREATE FUNCTION other.refuse_renewals() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'refused by the test'; END $$;
    CREATE TRIGGER refuse_renewals BEFORE UPDATE ON other.inbox FOR EACH ROW
        EXECUTE FUNCTION other.refuse_renewals()
"""
SHORT: Final = timedelta(seconds=1)
"""A lease the slow handlers outlive: renewed every third of it."""
_ROWS: Final = TypeAdapter(list[tuple[object, ...]])
_MOMENT: Final = TypeAdapter(datetime)
_IDS: Final = TypeAdapter(list[uuid.UUID])


class UnreachableError(UnavailableError):
    """A test's dependency that is down."""

    code: ClassVar[str] = "test_outside_unreachable"

    def __init__(self) -> None:
        super().__init__("the test's dependency is down")


class UnreadableError(InvalidInputError):
    """A test's event that can never be handled."""

    code: ClassVar[str] = "test_outside_unreadable"

    def __init__(self) -> None:
        super().__init__("the test's event cannot be read")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = RENAMED
    VERSION: ClassVar[int] = 1
    name: str


@final
class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@final
class Probe:
    """Another module's API: a facade that writes in its own unit (the probe's schema)."""

    __slots__ = ("_database",)

    def __init__(self, database: BoundDatabase) -> None:
        self._database = database

    @facade
    async def seen(self, call: SystemCall, event: uuid.UUID) -> None:
        with acting(call):
            async with self._database.unit(call.scope) as unit:
                unit.audit(action="probe.rename_seen", subject=event)


@final
@dataclass(slots=True)
class Handler:
    """An outside handler: it calls the probe's facade as its consumer, then fails the events the test names.

    ``during`` is awaited after the facade call and before a failure: a test holds the handler there.
    """

    probe: Probe
    calls: list[uuid.UUID] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    failures: dict[uuid.UUID, list[Exception]] = field(default_factory=dict)
    during: Callable[[], Awaitable[None]] | None = None
    cancelled: int = 0

    def fail(self, event: uuid.UUID, *errors: Exception) -> None:
        self.failures[event] = list(errors)

    async def __call__(self, event: RawEvent) -> None:
        self.calls.append(event.id)
        assert open_unit() is None
        self.actors.append(current_actor())
        call = SystemCall(reason=f"consumer:{NAME}", scope=event.scope, call_id=str(event.id))
        await self.probe.seen(call, event.id)
        try:
            if self.during is not None:
                await self.during()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        pending = self.failures.get(event.id)
        if pending:
            raise pending.pop(0)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Bench:
    """The producer, the consuming module, an outside handler, and a superuser to look and move rows."""

    pool: Database
    probe: BoundDatabase
    other: BoundDatabase
    superuser: psycopg.AsyncConnection
    registry: CollectorRegistry
    handler: Handler

    def runner(
        self, *, max_attempts: int = 10, lease: timedelta | None = None, tracing: Tracing | None = None
    ) -> InboxRunner:
        declared = ConsumerDecl(consumer=NAME, types=(RENAMED,), max_attempts=max_attempts, style="outside")
        consumer = Consumer.of(ConsumerSpec(name=NAME, outside=self.handler), declared)
        if lease is None:
            return InboxRunner(
                database=self.other, consumer=consumer, tracing=tracing, registry=self.registry
            )
        return InboxRunner(
            database=self.other, consumer=consumer, lease=lease, tracing=tracing, registry=self.registry
        )

    async def deliver(self, *events: Event) -> list[uuid.UUID]:
        """Publish ``events`` in one unit and fan them out; their ids, in order."""
        async with self.probe.unit(TENANT) as uow:
            for event in events:
                uow.publish(event)
        async with self.probe.unit(None) as uow:
            found = (await uow.connection.execute(text("SELECT id FROM probe.outbox ORDER BY id"))).scalars()
            ids = _IDS.validate_python(found.all())
        dispatcher = Dispatcher(self.pool, schemas=("probe", "other"), signals=Silent())
        assert await dispatcher.run_until_idle() == len(events)
        return ids

    async def rows(self, query: LiteralString, *parameters: object) -> list[tuple[object, ...]]:
        cursor = await self.superuser.execute(query, parameters)
        return _ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])

    async def state(self, event: uuid.UUID) -> tuple[object, ...]:
        (found,) = await self.rows(STATE, event)
        return found

    async def seen(self) -> list[tuple[object, ...]]:
        return await self.rows("SELECT actor, call_id, action FROM probe.audit_events ORDER BY id")

    async def make_due(self) -> None:
        await self.superuser.execute(
            "UPDATE other.inbox SET next_attempt_at = now() - interval '1 millisecond'"
        )

    async def now(self) -> datetime:
        ((moment,),) = await self.rows("SELECT clock_timestamp()")
        return _MOMENT.validate_python(moment)

    def handled(self, outcome: str) -> float | None:
        return self.registry.get_sample_value(
            "ledger_inbox_handled_total", {"consumer": NAME, "outcome": outcome}
        )


@pytest.fixture
def bench(
    pool: Database,
    probe: BoundDatabase,
    other: BoundDatabase,
    superuser: psycopg.AsyncConnection,
    subscribe: Subscribe,
) -> Bench:
    """The bench of a test, with a metrics registry of its own."""
    subscribe("other", {NAME: frozenset({RENAMED})})
    return Bench(
        pool=pool,
        probe=probe,
        other=other,
        superuser=superuser,
        registry=CollectorRegistry(),
        handler=Handler(Probe(probe)),
    )


@pytest.fixture
async def takeover(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A second superuser session: another worker taking a row over while the first runs its handler."""
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


def renamed(name: str = "x") -> Renamed:
    return Renamed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name=name)


async def test_an_outside_handler_calls_a_facade_and_its_row_is_deleted_under_its_lease(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner = bench.runner()
    assert await runner.run_until_idle() == 1
    assert bench.handler.calls == [event]
    assert bench.handler.actors == [Actor(ACTOR, str(event))]
    assert await bench.seen() == [(ACTOR, str(event), "probe.rename_seen")]
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert (bench.handled("done"), bench.handled("lease_lost")) == (1, 0)
    assert await runner.run_until_idle() == 0


async def test_an_inside_handler_still_may_not_call_a_facade(bench: Bench) -> None:
    """Rule 13 for the consumers that keep their unit: a facade call is refused, a failure like any other."""
    (event,) = await bench.deliver(renamed())

    async def inside(raw: RawEvent, unit: UnitOfWork) -> None:
        call = SystemCall(reason=f"consumer:{NAME}", scope=unit.scope, call_id=str(raw.id))
        await bench.handler.probe.seen(call, raw.id)

    declared = ConsumerDecl(consumer=NAME, types=(RENAMED,))
    consumer = Consumer.of(ConsumerSpec(name=NAME, handler=inside), declared)
    with capture_logs():
        assert await InboxRunner(database=bench.other, consumer=consumer).run_until_idle() == 1
    assert await bench.state(event) == ("ready", 1, UnitOpenError.code)
    assert await bench.seen() == []


async def test_a_handled_event_runs_in_a_span_that_continues_its_trace(
    bench: Bench, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    first, second = await bench.deliver(renamed("1"), renamed("2"))
    bench.handler.fail(second, UnreachableError())
    with capture_logs():
        assert await bench.runner(tracing=tracing).run_until_idle() == 2
    spans = [span for span in exporter.get_finished_spans() if span.name == NAME]
    assert [span.status.status_code for span in spans] == [StatusCode.UNSET, StatusCode.ERROR]
    assert bench.handler.calls == [first, second]


async def test_a_retryable_failure_leaves_the_row_ready_after_the_backoff(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, UnreachableError())
    runner = bench.runner()
    before = await bench.now()
    with capture_logs() as logs:
        assert await runner.run_until_idle() == 1
    after = await bench.now()
    (row,) = await bench.rows(
        "SELECT status, attempts, last_error_code, next_attempt_at, lease_until, lease_token FROM other.inbox"
    )
    assert row[:3] == ("ready", 1, "test_outside_unreachable")
    assert before + timedelta(seconds=1) <= _MOMENT.validate_python(row[3]) <= after + timedelta(seconds=1)
    assert row[4:] == (None, None)
    assert logs == [
        {
            "event": "inbox.handler_failed",
            "consumer": NAME,
            "event_id": str(event),
            "event_type": RENAMED,
            "attempts": 1,
            "dead": False,
            "exc_info": True,
            "log_level": "error",
        }
    ]
    assert bench.handled("retry") == 1
    assert await runner.run_until_idle() == 0  # not due yet
    await bench.make_due()
    assert await runner.run_until_idle() == 1
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert bench.handler.calls == [event, event]
    assert len(await bench.seen()) == 2  # an outside handler's effects stay: its facade is idempotent


async def test_an_invalid_event_is_dead_at_once_and_a_replay_runs_it_again(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, UnreadableError())
    runner = bench.runner()
    with capture_logs():
        assert await runner.run_until_idle() == 1
    assert await bench.state(event) == ("dead", 1, "test_outside_unreadable")
    assert bench.handled("dead") == 1
    await bench.make_due()
    assert await runner.run_until_idle() == 0  # dead rows are never claimed
    assert await runner.replay(event)
    assert await runner.run_until_idle() == 1
    assert await bench.rows("SELECT * FROM other.inbox") == []


async def test_a_poison_event_is_dead_once_its_attempts_are_spent(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, RuntimeError("boom"), RuntimeError("boom"))
    runner = bench.runner(max_attempts=2)
    with capture_logs():
        assert await runner.run_until_idle() == 1
        assert await bench.state(event) == ("ready", 1, "internal")
        await bench.make_due()
        assert await runner.run_until_idle() == 1
    assert await bench.state(event) == ("dead", 2, "internal")
    assert (bench.handled("retry"), bench.handled("dead")) == (1, 1)


async def test_a_handler_slower_than_its_lease_keeps_its_row(bench: Bench) -> None:
    """The lease is renewed while the handler runs: another runner never takes the row over meanwhile."""
    (event,) = await bench.deliver(renamed())
    runner_a, runner_b = bench.runner(lease=SHORT), bench.runner(lease=SHORT)
    (claimed,) = await runner_a.claim_now()
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        entered.set()
        await release.wait()

    bench.handler.during = hold
    handling = asyncio.create_task(runner_a.handle(claimed))
    await entered.wait()
    outlived = await bench.now() + 2 * SHORT

    async def past_the_first_lease() -> bool:
        return await bench.now() > outlived

    await waiting.eventually(past_the_first_lease)
    assert await runner_b.claim_now() == []
    ((lease_until,),) = await bench.rows("SELECT lease_until FROM other.inbox")
    assert _MOMENT.validate_python(lease_until) > await bench.now()
    release.set()
    await handling
    assert bench.handler.calls == [event]
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert (bench.handled("done"), bench.handled("lease_lost")) == (1, 0)


async def test_a_row_taken_over_before_its_handler_starts_is_not_handled(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner_a, runner_b = bench.runner(), bench.runner()
    (claimed,) = await runner_a.claim_now()
    await bench.superuser.execute(EXPIRE)
    assert await runner_b.run_until_idle() == 1  # taken over and handled
    with capture_logs() as logs:
        await runner_a.handle(claimed)
    assert [entry["event"] for entry in logs] == ["inbox_lease_lost"]
    assert bench.handler.calls == [event]  # A never ran it: the fenced extension found another token
    assert (bench.handled("done"), bench.handled("lease_lost")) == (1, 1)


async def test_a_runner_whose_lease_was_taken_while_its_handler_ran_deletes_nothing(
    bench: Bench, takeover: psycopg.AsyncConnection
) -> None:
    """The handler ran (its effects stay: at least once), but the row is the new holder's: it stays."""
    await bench.deliver(renamed())

    async def taken_over() -> None:
        await takeover.execute(TAKE_OVER)

    bench.handler.during = taken_over
    with capture_logs() as logs:
        assert await bench.runner().run_until_idle() == 1
    assert [entry["event"] for entry in logs] == ["inbox_lease_lost"]
    (row,) = await bench.rows("SELECT status, attempts, lease_token IS NOT NULL FROM other.inbox")
    assert row == ("running", 1, True)  # the new holder's, untouched
    assert (bench.handled("done"), bench.handled("lease_lost")) == (0, 1)
    assert len(await bench.seen()) == 1


async def test_a_failure_after_the_lease_was_taken_is_not_recorded(
    bench: Bench, takeover: psycopg.AsyncConnection
) -> None:
    (event,) = await bench.deliver(renamed())

    async def taken_over() -> None:
        await takeover.execute(TAKE_OVER)

    bench.handler.during = taken_over
    bench.handler.fail(event, UnreachableError())
    with capture_logs() as logs:
        assert await bench.runner().run_until_idle() == 1
    assert [entry["event"] for entry in logs] == ["inbox.handler_failed", "inbox_lease_lost"]
    assert await bench.state(event) == ("running", 1, None)  # the new holder's, untouched
    assert (bench.handled("retry"), bench.handled("lease_lost")) == (0, 1)


async def test_a_lease_taken_over_while_the_handler_runs_stops_it(
    bench: Bench, takeover: psycopg.AsyncConnection
) -> None:
    (event,) = await bench.deliver(renamed())
    entered = asyncio.Event()

    async def stuck() -> None:
        entered.set()
        await asyncio.Event().wait()  # until the renewal finds the lease gone and stops it

    bench.handler.during = stuck
    handling = asyncio.create_task(bench.runner(lease=SHORT).run_until_idle())
    await entered.wait()
    await takeover.execute(TAKE_OVER)
    with capture_logs() as logs:
        async with asyncio.timeout(5):
            assert await handling == 1
    assert [entry["event"] for entry in logs] == ["inbox_lease_lost"]
    assert bench.handler.cancelled == 1
    assert await bench.state(event) == ("running", 1, None)
    assert (bench.handled("done"), bench.handled("lease_lost")) == (0, 1)


async def test_a_lease_that_cannot_be_renewed_stops_the_handler(bench: Bench) -> None:
    """A failed renewal cannot vouch for the lease: the handler is stopped as if it had been taken over."""
    (event,) = await bench.deliver(renamed())
    entered = asyncio.Event()

    async def stuck() -> None:
        entered.set()
        await asyncio.Event().wait()

    bench.handler.during = stuck
    handling = asyncio.create_task(bench.runner(lease=SHORT).run_until_idle())
    await entered.wait()
    await bench.superuser.execute(REFUSE_RENEWALS)
    try:
        with capture_logs() as logs:
            async with asyncio.timeout(5):
                assert await handling == 1
    finally:
        await bench.superuser.execute("DROP FUNCTION other.refuse_renewals() CASCADE")
    assert [entry["event"] for entry in logs] == ["inbox_lease_renewal_failed", "inbox_lease_lost"]
    assert bench.handler.cancelled == 1
    assert await bench.state(event) == ("running", 1, None)  # left to its lease: recovered by the next claim


async def test_a_worker_that_died_mid_handler_leaves_the_row_to_be_handled_again(bench: Bench) -> None:
    """At least once: the handler runs again after the lease ran out, and the row goes once."""
    (event,) = await bench.deliver(renamed())
    runner = bench.runner()
    await runner.claim_now()  # the claiming worker dies before it records anything
    await bench.superuser.execute(EXPIRE)
    with capture_logs():
        assert await runner.run_until_idle() == 1
    assert bench.handler.calls == [event]
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert bench.handled("done") == 1


async def test_a_cancelled_runner_gives_its_rows_back_and_the_started_one_keeps_its_attempt(
    bench: Bench,
) -> None:
    started, later = await bench.deliver(renamed("1"), renamed("2"))
    entered = asyncio.Event()

    async def stuck() -> None:
        entered.set()
        await asyncio.Event().wait()

    bench.handler.during = stuck
    running = asyncio.create_task(bench.runner().run_once())
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert bench.handler.cancelled == 1
    assert await bench.state(started) == ("ready", 1, None)
    assert await bench.state(later) == ("ready", 0, None)
    assert await bench.rows("SELECT count(*) FROM other.inbox WHERE lease_token IS NOT NULL") == [(0,)]
