"""The inbox runner on real PostgreSQL (§3.5): leases, fencing, retries, dead rows, ordered consumers.

Rows are made due by moving their columns (as the superuser), never by a clock: due and lease comparisons
are the database's (``now()``). Absence is proved by draining.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import Callable, Mapping

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
from ledger_platform.events import Consumer, Dispatcher, InboxRunner
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events import RawEvent
lazy from ledger_platform.observability.tracing import Tracing

type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]

TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
S, T = uuid.uuid7(), uuid.uuid7()
NAME: Final = "other.renames"
RENAMED: Final = "probe.thing.renamed"
CREATED: Final = "probe.thing.created"
SCHEMAS: Final = ("probe", "other")
STATE: Final = "SELECT status, attempts, last_error_code FROM other.inbox WHERE event_id = %s"
EXPIRE: Final = "UPDATE other.inbox SET lease_until = now() - interval '1 millisecond'"
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

    def fail(self, event: uuid.UUID, *errors: Exception) -> None:
        self.failures[event] = list(errors)

    async def __call__(self, event: RawEvent, unit: UnitOfWork) -> None:
        self.calls.append(event.id)
        user = _TEXT.validate_python(
            (await unit.connection.execute(text("SELECT current_user"))).scalar_one()
        )
        self.seen.append((event, unit.scope, user))
        unit.audit(action="other.rename_seen", subject=event.id)
        pending = self.failures.get(event.id)
        if pending:
            raise pending.pop(0)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Bench:
    """The producer, the consuming module, a recording handler, and a superuser to look and move rows."""

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
        dispatcher = Dispatcher(self.probe.database, schemas=SCHEMAS, signals=Silent())
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

    def handled(self, outcome: str) -> float | None:
        return self.registry.get_sample_value(
            "ledger_inbox_handled_total", {"consumer": NAME, "outcome": outcome}
        )


def renamed(subject: uuid.UUID = S, name: str = "x") -> Renamed:
    return Renamed(scope=TENANT, subject=subject, aggregate_version=1, name=name)


@pytest.fixture
def bench(
    probe: BoundDatabase,
    other: BoundDatabase,
    superuser: psycopg.AsyncConnection,
    registry: CollectorRegistry,
    subscribe: Subscribe,
) -> Bench:
    subscribe("other", {NAME: frozenset({RENAMED, CREATED})})
    return Bench(probe=probe, other=other, superuser=superuser, registry=registry, subscribe=subscribe)


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
    assert bench.handler.calls == [event, event]


async def test_a_failure_recorded_after_the_lease_was_lost_changes_nothing(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
    runner_a, runner_b = bench.runner(), bench.runner()
    (claimed,) = await runner_a.claim_now()
    await bench.superuser.execute(EXPIRE)
    (taken,) = await runner_b.claim_now()  # B holds it now, with another token
    bench.handler.fail(event, UnreachableError())
    with capture_logs() as logs:
        await runner_a.handle(claimed)
    assert [entry["event"] for entry in logs] == ["inbox.handler_failed", "inbox_lease_lost"]
    assert await bench.state(event) == ("running", 2, None)
    assert bench.handled("lease_lost") == 1
    await runner_b.handle(taken)
    assert await bench.rows("SELECT * FROM other.inbox") == []


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


async def test_a_cancelled_handling_is_raised_and_leaves_its_row_to_the_lease(bench: Bench) -> None:
    (event,) = await bench.deliver(renamed())
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
    assert await bench.state(event) == ("running", 1, None)
    assert (bench.handled("retry"), bench.handled("dead")) == (0, 0)
