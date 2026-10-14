"""The inbox runner on real PostgreSQL (§3.5): leases, fencing, retries, dead rows, ordered consumers.

Rows are made due by moving their columns (as the superuser), never by a clock: due and lease comparisons
are the database's (``now()``). Absence is proved by draining.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import pytest
from opentelemetry.trace import StatusCode
from pydantic import TypeAdapter
from sqlalchemy import text
from structlog.testing import capture_logs
lazy import psycopg
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import InvalidInputError, UnavailableError
from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.events.consumers import Consumer
from ledger_platform.events.dispatcher import Dispatcher
from ledger_platform.events.inbox import InboxRunner, ReplayRefusedError
from ledger_platform.events.wakeups import Wakeups
from ledger_testing import waiting
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Channel, Signals
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events import RawEvent
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.postgres import WorkerDatabase

type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]

TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
S, T = uuid.uuid7(), uuid.uuid7()
NAME: Final = "other.renames"
RENAMED: Final = "probe.thing.renamed"
CREATED: Final = "probe.thing.created"
SCHEMAS: Final = ("probe", "other")
STATE: Final = "SELECT status, attempts, last_error_code FROM other.inbox WHERE event_id = %s"
EXPIRE: Final = "UPDATE other.inbox SET lease_until = now() - interval '1 hour'"
"""Long enough ago that any backoff since has passed too: the row is due again at once."""
JUST_EXPIRED: Final = "UPDATE other.inbox SET lease_until = now() - interval '1 millisecond'"
TAKE_OVER: Final = (
    "UPDATE other.inbox SET lease_token = gen_random_uuid(), lease_until = now() + interval '1 minute'"
)
LOCK_WAITERS: Final = (
    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity"
    " WHERE datname = current_database() AND wait_event_type = 'Lock')"
)
LEASED: Final = "SELECT count(*) FROM other.inbox WHERE lease_token IS NOT NULL OR lease_until IS NOT NULL"
_ROWS: Final = TypeAdapter(list[tuple[object, ...]])
_MOMENT: Final = TypeAdapter(datetime)
_IDS: Final = TypeAdapter(list[uuid.UUID])
_TEXT: Final = TypeAdapter(str)


class UnreachableError(UnavailableError):
    """A test's dependency that is down."""

    code: ClassVar[str] = "test_unreachable"

    def __init__(self) -> None:
        super().__init__("the test's dependency is down")


class UnreadableError(InvalidInputError):
    """A test's event that can never be handled."""

    code: ClassVar[str] = "test_unreadable"

    def __init__(self) -> None:
        super().__init__("the test's event cannot be read")


class CleanupFailedError(RuntimeError):
    """A clean-up that failed while the task was being stopped."""

    def __init__(self) -> None:
        super().__init__("a clean-up failed while stopping")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = RENAMED
    VERSION: ClassVar[int] = 1
    name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Created(Event):
    TYPE: ClassVar[str] = CREATED
    VERSION: ClassVar[int] = 1


@final
class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@final
@dataclass(slots=True)
class Handler:
    """Records every event it is given, and fails the ones the test names (each as many times as it says)."""

    calls: list[uuid.UUID] = field(default_factory=list)
    failures: dict[uuid.UUID, list[Exception]] = field(default_factory=dict)
    seen: list[tuple[RawEvent, Scope | None, object]] = field(default_factory=list)
    during: Callable[[], Awaitable[None]] | None = None
    """Awaited inside the unit, after the audit and before a failure: a test holds the handler there."""

    def fail(self, event: uuid.UUID, *errors: Exception) -> None:
        self.failures[event] = list(errors)

    async def __call__(self, event: RawEvent, unit: UnitOfWork) -> None:
        self.calls.append(event.id)
        user = _TEXT.validate_python(
            (await unit.connection.execute(text("SELECT current_user"))).scalar_one()
        )
        self.seen.append((event, unit.scope, user))
        unit.audit(action="other.rename_seen", subject=event.id)
        if self.during is not None:
            await self.during()
        pending = self.failures.get(event.id)
        if pending:
            raise pending.pop(0)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Bench:
    """The producer, the consuming module, a recording handler, and a superuser to look and move rows."""

    pool: Database
    """The pool every schema's units share (what a dispatcher runs on)."""
    probe: BoundDatabase
    other: BoundDatabase
    superuser: psycopg.AsyncConnection
    registry: CollectorRegistry
    subscribe: Subscribe
    handler: Handler = field(default_factory=Handler)

    def runner(
        self, *, max_attempts: int = 10, ordered: bool = False, tracing: Tracing | None = None
    ) -> InboxRunner:
        consumer = Consumer(
            name=NAME,
            types=frozenset({RENAMED}),
            max_attempts=max_attempts,
            ordered=ordered,
            handler=self.handler,
        )
        return InboxRunner(database=self.other, consumer=consumer, tracing=tracing, registry=self.registry)

    async def deliver(self, *events: Event) -> list[uuid.UUID]:
        """Publish ``events`` in one unit and fan them out; their ids, in order."""
        async with self.probe.unit(TENANT) as uow:
            for event in events:
                uow.publish(event)
        async with self.probe.unit(None) as uow:
            found = (await uow.connection.execute(text("SELECT id FROM probe.outbox ORDER BY id"))).scalars()
            ids = _IDS.validate_python(found.all())
        dispatcher = Dispatcher(self.pool, schemas=SCHEMAS, signals=Silent())
        assert await dispatcher.run_until_idle() == len(events)
        return ids

    async def rows(self, query: LiteralString, *parameters: object) -> list[tuple[object, ...]]:
        cursor = await self.superuser.execute(query, parameters)
        return _ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])

    async def state(self, event: uuid.UUID) -> tuple[object, ...]:
        (found,) = await self.rows(STATE, event)
        return found

    async def make_due(self) -> None:
        await self.superuser.execute(
            "UPDATE other.inbox SET next_attempt_at = now() - interval '1 millisecond'"
        )

    async def now(self) -> datetime:
        ((moment,),) = await self.rows("SELECT clock_timestamp()")
        return _MOMENT.validate_python(moment)

    def oldest_unhandled(self) -> float | None:
        return self.registry.get_sample_value("ledger_inbox_unhandled_oldest_seconds", {"consumer": NAME})

    def handled(self, outcome: str) -> float | None:
        return self.registry.get_sample_value(
            "ledger_inbox_handled_total", {"consumer": NAME, "outcome": outcome}
        )


async def waiting_for(check: Callable[[], Awaitable[bool]]) -> None:
    """Until ``check`` holds (``ledger_testing.waiting.eventually``: a bound, not a delay)."""
    await waiting.eventually(check)


@pytest.fixture
async def takeover(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A second superuser session: another worker taking a row over while the first holds it."""
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


def renamed(subject: uuid.UUID | None = S, name: str = "x", version: int | None = 1) -> Renamed:
    return Renamed(scope=TENANT, subject=subject, aggregate_version=version, name=name)


@pytest.fixture
def bench(
    pool: Database,
    probe: BoundDatabase,
    other: BoundDatabase,
    superuser: psycopg.AsyncConnection,
    subscribe: Subscribe,
) -> Bench:
    """The bench of a test, with a metrics registry of its own."""
    subscribe("other", {NAME: frozenset({RENAMED, CREATED})})
    return Bench(
        pool=pool,
        probe=probe,
        other=other,
        superuser=superuser,
        registry=CollectorRegistry(),
        subscribe=subscribe,
    )


async def test_a_handler_runs_once_in_its_modules_role_and_scope_and_its_row_goes_with_its_writes(
    bench: Bench,
) -> None:
    (event,) = await bench.deliver(renamed())
    runner = bench.runner()
    assert await runner.run_until_idle() == 1
    ((raw, scope, user),) = bench.handler.seen
    assert (raw.id, raw.type, raw.payload, raw.scope, raw.subject) == (
        event,
        RENAMED,
        {"name": "x"},
        TENANT,
        S,
    )
    assert (scope, user) == (TENANT, "other_rw")
    assert await bench.rows("SELECT actor, call_id, action FROM other.audit_events") == [
        ("system:consumer:other.renames", str(event), "other.rename_seen")
    ]
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert bench.handled("done") == 1
    assert bench.handled("lease_lost") == 0
    assert await runner.run_until_idle() == 0


async def test_a_handled_event_runs_in_a_span_that_continues_its_trace(
    bench: Bench, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    first, second = await bench.deliver(renamed(S), renamed(T))
    bench.handler.fail(second, UnreachableError())
    assert await bench.runner(tracing=tracing).run_until_idle() == 2
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == [NAME, NAME]
    assert [span.status.status_code for span in spans] == [StatusCode.UNSET, StatusCode.ERROR]
    assert bench.handler.calls == [first, second]


async def test_a_failing_handler_is_retried_with_backoff(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, UnreachableError(), UnreachableError())
    runner = bench.runner()
    before = await bench.now()
    with capture_logs() as logs:
        assert await runner.run_until_idle() == 1
    after = await bench.now()
    (row,) = await bench.rows(
        "SELECT status, attempts, last_error_code, next_attempt_at, lease_until, lease_token FROM other.inbox"
    )
    assert row[:3] == ("ready", 1, "test_unreachable")
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
    assert await runner.run_until_idle() == 0  # not due yet
    await bench.make_due()
    before = await bench.now()
    assert await runner.run_until_idle() == 1
    after = await bench.now()
    ((status, attempts, due),) = await bench.rows("SELECT status, attempts, next_attempt_at FROM other.inbox")
    assert (status, attempts) == ("ready", 2)
    assert before + timedelta(seconds=10) <= _MOMENT.validate_python(due) <= after + timedelta(seconds=10)
    assert bench.handled("retry") == 2
    await bench.make_due()
    assert await runner.run_until_idle() == 1
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert bench.handler.calls == [event, event, event]
    assert await bench.rows("SELECT count(*) FROM other.audit_events") == [(1,)]  # only the commit's


async def test_an_invalid_event_is_dead_at_once_and_kept(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, UnreadableError())
    runner = bench.runner()
    assert await runner.run_until_idle() == 1
    assert await bench.state(event) == ("dead", 1, "test_unreadable")
    assert bench.handled("dead") == 1
    await bench.make_due()
    assert await runner.run_until_idle() == 0  # dead rows are never claimed


async def test_after_max_attempts_the_row_is_dead_and_a_replay_runs_it_again(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    bench.handler.fail(event, RuntimeError("boom"), RuntimeError("boom"))
    runner = bench.runner(max_attempts=2)
    assert await runner.run_until_idle() == 1
    assert await bench.state(event) == ("ready", 1, "internal")
    await bench.make_due()
    assert await runner.run_until_idle() == 1
    assert await bench.state(event) == ("dead", 2, "internal")
    assert not await runner.replay(uuid.uuid7())  # no such row
    assert await runner.replay(event)
    assert await bench.state(event) == ("ready", 0, None)
    assert not await runner.replay(event)  # only a dead row is replayed
    assert await runner.run_until_idle() == 1
    assert await bench.rows("SELECT * FROM other.inbox") == []


async def test_a_handler_that_lost_its_lease_commits_nothing(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner_a, runner_b = bench.runner(), bench.runner()
    (claimed,) = await runner_a.claim_now()
    assert claimed.event.id == event
    assert await runner_b.run_until_idle() == 0  # the lease holds
    await bench.superuser.execute(EXPIRE)
    assert await runner_b.run_until_idle() == 1  # taken over and handled
    with capture_logs() as logs:
        await runner_a.handle(claimed)
    assert [entry["event"] for entry in logs] == ["inbox_lease_lost"]
    assert bench.handled("lease_lost") == 1
    assert bench.handled("done") == 1
    assert await bench.rows("SELECT count(*) FROM other.audit_events") == [(1,)]
    assert bench.handler.calls == [event]  # A never ran it: the fenced extension found another token


async def test_a_runner_whose_lease_ran_out_but_was_not_taken_keeps_its_row(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner_a, runner_b = bench.runner(), bench.runner()
    (claimed,) = await runner_a.claim_now()
    await bench.superuser.execute(EXPIRE)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        entered.set()
        await release.wait()

    bench.handler.during = hold
    handling = asyncio.create_task(runner_a.handle(claimed))
    await entered.wait()  # A extended its lease first, and holds the row while it works
    assert await runner_b.claim_now() == []
    release.set()
    await handling
    assert bench.handler.calls == [event]
    assert await bench.rows("SELECT * FROM other.inbox") == []
    assert (bench.handled("done"), bench.handled("lease_lost")) == (1, 0)


async def test_a_failure_is_recorded_before_the_row_is_released(
    bench: Bench, takeover: psycopg.AsyncConnection
) -> None:
    """A worker queued for the row while the handler failed gets it only after the failure is recorded."""
    (event,) = await bench.deliver(renamed())
    (claimed,) = await bench.runner().claim_now()
    waiting: list[asyncio.Task[object]] = []

    async def taken_over_meanwhile() -> None:
        """Another worker queues for the row (it is A's while A's unit runs) and gets it at A's rollback."""
        waiting.append(asyncio.create_task(takeover.execute(TAKE_OVER)))

        async def queued() -> bool:
            ((found,),) = await bench.rows(LOCK_WAITERS)
            return found is True

        await waiting_for(queued)

    bench.handler.during = taken_over_meanwhile
    bench.handler.fail(event, UnreachableError())
    with capture_logs() as logs:
        await bench.runner().handle(claimed)
    await waiting[0]
    assert [entry["event"] for entry in logs] == ["inbox.handler_failed"]
    assert await bench.state(event) == ("ready", 1, "test_unreachable")  # the real code, then the takeover's
    assert (bench.handled("lease_lost"), bench.handled("retry")) == (0, 1)


async def test_an_ordered_consumer_handles_a_subjects_events_in_order(bench: Bench) -> None:
    e1, e2, e3 = await bench.deliver(renamed(S, "1"), renamed(S, "2"), renamed(T, "3"))
    bench.handler.fail(e1, UnreachableError())
    runner = bench.runner(ordered=True)
    assert await runner.run_once() == 2
    assert await bench.state(e1) == ("ready", 1, "test_unreachable")
    assert await bench.state(e2) == ("ready", 0, None)  # behind its subject's head
    assert await bench.rows(STATE, e3) == []
    assert await runner.run_until_idle() == 0  # the head is not due: its subject waits
    await bench.make_due()
    assert await runner.run_until_idle() == 2
    assert bench.handler.calls == [e1, e3, e1, e2]


async def test_an_unordered_consumer_does_not_wait_for_a_subjects_head(bench: Bench) -> None:
    e1, e2 = await bench.deliver(renamed(S, "1"), renamed(S, "2"))
    bench.handler.fail(e1, UnreachableError())
    assert await bench.runner().run_once() == 2
    assert bench.handler.calls == [e1, e2]


async def test_a_dead_row_does_not_block_its_subject(bench: Bench) -> None:
    e1, e2 = await bench.deliver(renamed(S, "1"), renamed(S, "2"))
    bench.handler.fail(e1, UnreadableError())
    assert await bench.runner(ordered=True).run_until_idle() == 2
    assert bench.handler.calls == [e1, e2]
    assert await bench.state(e1) == ("dead", 1, "test_unreadable")


async def test_a_runner_never_claims_a_type_it_has_no_handler_for(bench: Bench) -> None:
    (event,) = await bench.deliver(Created(scope=TENANT, subject=S, aggregate_version=1))
    assert await bench.runner().run_until_idle() == 0
    assert await bench.state(event) == ("ready", 0, None)
    assert bench.handler.calls == []


async def test_another_consumers_rows_are_never_claimed(bench: Bench) -> None:
    bench.subscribe("other", {"other.echo": frozenset({RENAMED})})
    await bench.deliver(renamed())
    assert await bench.runner().run_until_idle() == 0
    assert await bench.rows("SELECT consumer, status, attempts FROM other.inbox") == [
        ("other.echo", "ready", 0)
    ]


async def test_a_cancelled_batch_is_raised_and_gives_its_rows_back(bench: Bench) -> None:
    """The row whose handler was stopped keeps its attempt; the rows never started get theirs back."""
    event, later = await bench.deliver(renamed(S, "1"), renamed(T, "2"))
    started, release = asyncio.Event(), asyncio.Event()

    async def stubborn(raw: RawEvent, unit: UnitOfWork) -> None:
        del raw, unit
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            raise CleanupFailedError from None

    consumer = Consumer(
        name=NAME, types=frozenset({RENAMED}), max_attempts=10, ordered=False, handler=stubborn
    )
    runner = InboxRunner(database=bench.other, consumer=consumer, registry=bench.registry)
    running = asyncio.create_task(runner.run_once())
    await started.wait()
    running.cancel()
    with pytest.raises(CleanupFailedError):
        await running
    assert await bench.state(event) == ("ready", 1, None)
    assert await bench.state(later) == ("ready", 0, None)
    assert await bench.rows(LEASED) == [(0,)]
    assert (bench.handled("retry"), bench.handled("dead")) == (0, 0)


async def test_a_stopped_runner_finishes_its_row_and_gives_the_rest_back_unspent(bench: Bench) -> None:
    first, second, third = await bench.deliver(renamed(S, "1"), renamed(T, "2"), renamed(None, "3"))
    entered, go_on = asyncio.Event(), asyncio.Event()

    async def held() -> None:
        entered.set()
        await go_on.wait()

    bench.handler.during = held
    runner = bench.runner()
    running = asyncio.create_task(runner.run(Wakeups("postgresql://never.invalid/ledger")))
    await entered.wait()
    runner.stop()
    go_on.set()
    async with asyncio.timeout(5):
        await runner.drained.wait()
    assert bench.handler.calls == [first]
    assert await bench.rows("SELECT event_id FROM other.inbox WHERE event_id = %s", first) == []
    assert await bench.state(second) == ("ready", 0, None)
    assert await bench.state(third) == ("ready", 0, None)
    assert await bench.rows(LEASED) == [(0,)]
    assert not running.done()  # a stopped worker waits to be cancelled: it never returns
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_a_runner_stopped_before_it_runs_claims_nothing(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner = bench.runner()
    runner.stop()
    running = asyncio.create_task(runner.run(Wakeups("postgresql://never.invalid/ledger")))
    async with asyncio.timeout(5):
        await runner.drained.wait()
    assert await bench.state(event) == ("ready", 0, None)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_an_ordered_consumer_follows_aggregate_versions_not_ids(bench: Bench) -> None:
    second, first = await bench.deliver(renamed(S, "2", version=2), renamed(S, "1", version=1))
    assert second < first  # published in the wrong order: the ids say second came first
    assert await bench.runner(ordered=True).run_until_idle() == 2
    assert bench.handler.calls == [first, second]


async def test_events_without_a_subject_are_not_ordered(bench: Bench) -> None:
    e1, e2 = await bench.deliver(renamed(None, "1"), renamed(None, "2"))
    bench.handler.fail(e1, UnreachableError())
    assert await bench.runner(ordered=True).run_once() == 2  # e2 does not wait behind e1
    assert bench.handler.calls == [e1, e2]


async def test_a_dead_row_is_not_replayed_while_an_earlier_row_of_its_subject_waits(bench: Bench) -> None:
    e1, e2 = await bench.deliver(renamed(S, "1"), renamed(S, "2"))
    runner = bench.runner(ordered=True)
    await bench.superuser.execute("UPDATE other.inbox SET status = 'dead' WHERE event_id = %s", (e2,))
    with pytest.raises(ReplayRefusedError) as refused:
        await runner.replay(e2)
    assert refused.value.code == "inbox_replay_refused"
    assert await bench.state(e2) == ("dead", 0, None)
    assert await runner.run_until_idle() == 1  # e1; the dead e2 is not claimed
    assert await runner.replay(e2)
    assert await runner.run_until_idle() == 1
    assert bench.handler.calls == [e1, e2]


async def test_an_unordered_consumer_replays_whatever_waits_before_it(bench: Bench) -> None:
    e1, e2 = await bench.deliver(renamed(S, "1"), renamed(S, "2"))
    await bench.superuser.execute("UPDATE other.inbox SET status = 'dead' WHERE event_id = %s", (e2,))
    assert await bench.runner().replay(e2)
    assert await bench.state(e1) == ("ready", 0, None)


@final
class OneWait:
    """A ``Waker`` that ends the loop at its first wait."""

    def subscribe(self, channel: Channel) -> asyncio.Event:
        del channel
        return asyncio.Event()

    async def wait(self, event: asyncio.Event, *, poll: float = 1.0) -> None:
        del event, poll
        raise LoopEndedError


class LoopEndedError(Exception):
    def __init__(self) -> None:
        super().__init__("the test ends the loop")


async def test_the_age_of_rows_no_handler_takes_is_exported(bench: Bench) -> None:
    runner = bench.runner()
    with pytest.raises(LoopEndedError):
        await runner.run(OneWait())
    assert bench.oldest_unhandled() == 0
    (event,) = await bench.deliver(Created(scope=TENANT, subject=S, aggregate_version=1))
    await bench.superuser.execute(
        "UPDATE other.inbox SET next_attempt_at = now() - interval '1 hour' WHERE event_id = %s", (event,)
    )
    with pytest.raises(LoopEndedError):
        await runner.run(OneWait())
    age = bench.oldest_unhandled()
    assert age is not None
    assert 3600 <= age < 3600 + 60


async def test_an_expired_lease_waits_out_the_backoff_and_a_spent_one_is_dead(bench: Bench) -> None:
    """I4: a row whose worker died is ready again after the backoff, or dead once its attempts are spent."""
    (event,) = await bench.deliver(renamed())
    runner = bench.runner(max_attempts=2)
    await runner.claim_now()
    await bench.superuser.execute(JUST_EXPIRED)
    with capture_logs() as recovered:
        assert await runner.claim_now() == []
    assert recovered == [
        {"event": "inbox_worker_recovered", "consumer": NAME, "rows": 1, "log_level": "warning"}
    ]
    assert bench.registry.get_sample_value("ledger_inbox_worker_recovered_total", {"consumer": NAME}) == 1
    assert await bench.state(event) == ("ready", 1, "worker_lost")
    await bench.make_due()
    (again,) = await runner.claim_now()
    assert again.attempts == 2
    await bench.superuser.execute(EXPIRE)
    with capture_logs() as logs:
        assert await runner.claim_now() == []
    assert await bench.state(event) == ("dead", 2, "worker_lost")
    assert logs == [{"event": "inbox_worker_lost", "consumer": NAME, "rows": 1, "log_level": "warning"}]
    assert bench.handled("dead") == 1
    assert bench.handler.calls == []


REFUSE_FOLLOW_UPS: Final = """
    CREATE FUNCTION other.refuse_jobs() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'refused by the test'; END $$;
    CREATE TRIGGER refuse_jobs BEFORE INSERT ON other.jobs FOR EACH ROW EXECUTE FUNCTION other.refuse_jobs()
"""


async def test_a_handler_whose_records_cannot_be_written_is_retried_and_the_runner_goes_on(
    bench: Bench, pool: Database, signals: Signals
) -> None:
    """The handler's enqueue is refused when the savepoint writes it: a failure like any other."""
    (event,) = await bench.deliver(renamed())
    following = BoundDatabase(
        pool,
        schema="other",
        source="other",
        jobs={"other.follow_up": JobPolicy(max_attempts=3, deadline=None)},
        signals=signals,
    )

    async def follow_up(raw: RawEvent, unit: UnitOfWork) -> None:
        unit.jobs.enqueue("other.follow_up", str(raw.id))

    consumer = Consumer(
        name=NAME, types=frozenset({RENAMED}), max_attempts=10, ordered=False, handler=follow_up
    )
    runner = InboxRunner(database=following, consumer=consumer, registry=bench.registry)
    await bench.superuser.execute(REFUSE_FOLLOW_UPS)
    try:
        with capture_logs() as logs:
            assert await runner.run_until_idle() == 1  # recorded, not raised
        assert [entry["event"] for entry in logs] == ["inbox.handler_failed"]
        assert await bench.state(event) == ("ready", 1, "internal")
        assert bench.handled("retry") == 1
    finally:
        await bench.superuser.execute("DROP FUNCTION other.refuse_jobs() CASCADE")
    await bench.make_due()
    assert await runner.run_until_idle() == 1
    assert await bench.rows("SELECT kind, key FROM other.jobs") == [("other.follow_up", str(event))]
