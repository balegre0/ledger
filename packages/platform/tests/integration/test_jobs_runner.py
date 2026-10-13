"""``JobRunner`` on real PostgreSQL: claims, outcomes, leases and fencing, attempts counted on claim (§3.6).

Time is the database's: a test makes a row due by moving its columns as admin (``run_at``, ``lease_until``),
never by waiting, and reads the times a record wrote from the row itself (``updated_at`` is the recording
unit's ``now()``).
"""

import asyncio
import contextlib
import uuid
from collections import Counter
from datetime import timedelta
from typing import Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Callable
lazy from datetime import datetime

import psycopg
import pytest
from opentelemetry import trace
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ledger_kernel.errors import ConflictError, InvalidInputError, UnavailableError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.jobs import Done, Job, JobOutcome, JobRunner, Park, RunAgain
from ledger_platform.runtime.manifest import JobDecl
from ledger_testing import waiting
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.postgres import WorkerDatabase

A, A1 = OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7())
TENANT: Final = Tenant(A, A1, Mode.TEST)
EXPORT: Final = "probe.export"
POLL: Final = "probe.poll"
LEASE: Final = timedelta(seconds=60)
DECLS: Final = {
    EXPORT: JobDecl(kind=EXPORT, lease=LEASE, max_attempts=3, deadline=timedelta(hours=1)),
    POLL: JobDecl(kind=POLL, lease=LEASE, max_attempts=5, concurrency=2),
}
STATE: Final = """
    SELECT status, attempts, last_error_code, parked_reason, lease_until, lease_token, run_at - updated_at
    FROM probe.jobs WHERE key = %s
"""
DUE: Final = "UPDATE probe.jobs SET run_at = now() - interval '1 second' WHERE status = 'ready'"
EXPIRED: Final = "UPDATE probe.jobs SET lease_until = now() - interval '1 second' WHERE status = 'running'"
UNLEASED: Final = (None, None)

type Answer = JobOutcome | Exception | Callable[[Job], JobOutcome]


class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@final
class Scripted:
    """A handler that answers, per key, what the test queued for it (``Done`` once the queue is empty)."""

    def __init__(self) -> None:
        self.answers: dict[str, list[Answer]] = {}
        self.seen: list[Job] = []

    def will(self, key: str, *answers: Answer) -> None:
        self.answers.setdefault(key, []).extend(answers)

    async def __call__(self, job: Job) -> JobOutcome:
        self.seen.append(job)
        queued = self.answers.get(job.key, [])
        answer = queued.pop(0) if queued else Done()
        if isinstance(answer, Exception):
            raise answer
        return answer(job) if callable(answer) else answer


def bind(database: Database) -> BoundDatabase:
    return BoundDatabase(
        database,
        schema="probe",
        source="probe",
        jobs={
            kind: JobPolicy(max_attempts=decl.max_attempts, deadline=decl.deadline)
            for kind, decl in DECLS.items()
        },
        signals=Silent(),
    )


@pytest.fixture
async def databases(chassis: WorkerDatabase) -> AsyncGenerator[tuple[Database, Database]]:
    """Two pools, as two worker processes would have."""
    first, second = (
        Database.create(app_url(chassis), pool_size=2),
        Database.create(app_url(chassis), pool_size=2),
    )
    try:
        yield first, second
    finally:
        await first.aclose()
        await second.aclose()


@pytest.fixture
def probe(databases: tuple[Database, Database]) -> BoundDatabase:
    return bind(databases[0])


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture
def handler() -> Scripted:
    return Scripted()


def runner_of(
    database: BoundDatabase, handler: Scripted, registry: CollectorRegistry, tracing: Tracing | None = None
) -> JobRunner:
    kinds = {kind: (decl, handler) for kind, decl in DECLS.items()}
    return JobRunner(database=database, kinds=kinds, registry=registry, tracing=tracing, poll=0.02)


@pytest.fixture
def runner(probe: BoundDatabase, handler: Scripted, registry: CollectorRegistry) -> JobRunner:
    return runner_of(probe, handler, registry)


async def enqueue(
    probe: BoundDatabase, *keys: str, kind: str = EXPORT, delay: timedelta | None = None
) -> datetime:
    """Jobs of ``kind`` for ``TENANT``, due now or after ``delay``; the enqueuing unit's time."""
    async with probe.unit(TENANT) as uow:
        for key in keys:
            uow.jobs.enqueue(
                kind, key, run_at=None if delay is None else uow.now + delay, payload={"key": key}
            )
    return uow.now


async def state(admin: psycopg.AsyncConnection, key: str) -> tuple[object, ...]:
    cursor = await admin.execute(STATE, (key,))
    (row,) = await cursor.fetchall()
    return tuple(row)


async def run(admin: psycopg.AsyncConnection, query: LiteralString) -> None:
    await admin.execute(query)


def finished(registry: CollectorRegistry, outcome: str, kind: str = EXPORT) -> float | None:
    return registry.get_sample_value("ledger_jobs_finished_total", {"kind": kind, "outcome": outcome})


async def test_a_due_job_runs_once_and_is_done(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    enqueued = await enqueue(probe, "k1")
    assert await runner.run_due() == 1
    (job,) = handler.seen
    assert (job.kind, job.key, job.scope, job.payload) == (EXPORT, "k1", TENANT, {"key": "k1"})
    assert (job.attempts, job.max_attempts, job.deadline_at, job.traceparent) == (
        1,
        3,
        enqueued + timedelta(hours=1),
        None,
    )
    assert (await state(admin, "k1"))[:6] == ("done", 1, None, None, *UNLEASED)
    assert await runner.run_due() == 0
    assert finished(registry, "done") == 1


async def test_a_job_before_its_time_waits(
    runner: JobRunner, probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    await enqueue(probe, "k1", delay=timedelta(minutes=5))
    assert await runner.run_due() == 0
    await run(admin, DUE)
    assert await runner.run_due() == 1


async def test_run_again_reschedules_without_counting_a_failure(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    enqueued = await enqueue(probe, "k1")
    again = enqueued + timedelta(minutes=30)
    handler.will("k1", RunAgain(again))
    assert await runner.run_due() == 1
    cursor = await admin.execute("SELECT status, run_at, attempts, lease_until, lease_token FROM probe.jobs")
    assert await cursor.fetchall() == [("ready", again, 0, *UNLEASED)]
    assert await runner.run_due() == 0
    await run(admin, DUE)
    assert await runner.run_due() == 1
    assert [finished(registry, "run_again"), finished(registry, "done")] == [1, 1]


async def test_park_waits_for_a_wake(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    await enqueue(probe, "k1")
    handler.will("k1", Park("certificate_missing"))
    assert await runner.run_due() == 1
    assert (await state(admin, "k1"))[:6] == ("parked", 1, None, "certificate_missing", *UNLEASED)
    await run(admin, "UPDATE probe.jobs SET run_at = now() - interval '30 days'")
    assert await runner.run_due() == 0
    async with probe.unit(TENANT) as uow:
        uow.jobs.wake_all(EXPORT, A1, "certificate_missing")
    assert await runner.run_due() == 1
    assert (await state(admin, "k1"))[0] == "done"
    assert finished(registry, "parked") == 1


async def test_a_retryable_error_backs_off_and_counts_on_claim(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    await enqueue(probe, "k1")
    handler.will("k1", UnavailableError(), UnavailableError())
    with capture_logs() as logs:
        assert await runner.run_due() == 1
    assert await state(admin, "k1") == ("ready", 1, "unavailable", None, *UNLEASED, timedelta(seconds=1))
    assert logs == [
        {"event": "job_failed", "kind": EXPORT, "code": "unavailable", "exc_info": True, "log_level": "error"}
    ]
    await run(admin, DUE)
    assert await runner.run_due() == 1
    assert await state(admin, "k1") == ("ready", 2, "unavailable", None, *UNLEASED, timedelta(seconds=10))
    assert finished(registry, "retry") == 2


async def test_invalid_and_conflict_errors_and_spent_attempts_are_dead(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    await enqueue(probe, "invalid", "conflict", "spent", "crashing")
    handler.will("invalid", InvalidInputError())
    handler.will("conflict", ConflictError())
    handler.will("spent", *(UnavailableError() for _ in range(3)))
    handler.will("crashing", LookupError())
    await runner.run_due()
    assert (await state(admin, "invalid"))[:3] == ("dead", 1, "invalid_input")
    assert (await state(admin, "conflict"))[:3] == ("dead", 1, "conflict")
    assert (await state(admin, "crashing"))[:3] == ("ready", 1, "internal")  # a bug's error is retried
    for attempt in (2, 3):
        await run(admin, DUE)
        await runner.run_due()
        assert (await state(admin, "spent"))[:2] == ("ready" if attempt < 3 else "dead", attempt)
    assert (await state(admin, "spent"))[:3] == ("dead", 3, "unavailable")
    cursor = await admin.execute("SELECT count(*) FROM probe.jobs WHERE status = 'dead'")
    assert await cursor.fetchall() == [(3,)]  # dead rows are kept
    assert finished(registry, "dead") == 3


async def test_a_job_past_its_deadline_is_dead(
    runner: JobRunner, probe: BoundDatabase, handler: Scripted, admin: psycopg.AsyncConnection
) -> None:
    await enqueue(probe, "polling", "failing")

    def beyond(job: Job) -> JobOutcome:
        assert job.deadline_at is not None
        return RunAgain(job.deadline_at + timedelta(seconds=1))

    handler.will("polling", beyond)
    handler.will("failing", UnavailableError())
    await run(admin, "UPDATE probe.jobs SET deadline_at = now() - interval '1 second' WHERE key = 'failing'")
    assert await runner.run_due() == 2
    assert (await state(admin, "polling"))[:3] == ("dead", 1, "deadline_passed")
    assert (await state(admin, "failing"))[:3] == ("dead", 1, "unavailable")


async def test_a_worker_that_crashed_mid_run_leaves_the_job_to_be_claimed_again(
    runner: JobRunner, probe: BoundDatabase, handler: Scripted, admin: psycopg.AsyncConnection
) -> None:
    await enqueue(probe, "k1")
    (claimed,) = await runner.claim_now()  # and never handled: the worker died
    assert await runner.run_due() == 0  # the lease holds
    await run(admin, EXPIRED)
    assert await runner.run_due() == 1
    (job,) = handler.seen
    assert job.lease_token != claimed.lease_token
    assert (await state(admin, "k1"))[:2] == ("done", 2)  # counted on each claim


async def test_a_job_that_keeps_killing_its_worker_dies(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    """Attempts are counted on claim, so a crash loop spends them: no fourth claim of a three-attempt kind."""
    await enqueue(probe, "k1")
    for attempt in (1, 2, 3):
        (job,) = await runner.claim_now()
        assert job.attempts == attempt
        await run(admin, EXPIRED)
    with capture_logs() as logs:
        assert await runner.claim_now() == []
    assert (await state(admin, "k1"))[:6] == ("dead", 3, "worker_lost", None, *UNLEASED)
    assert handler.seen == []
    assert logs == [{"event": "job_worker_lost", "kind": EXPORT, "jobs": 1, "log_level": "warning"}]
    assert finished(registry, "dead") == 1


async def test_a_zombie_records_nothing_after_its_lease_was_taken_over(
    probe: BoundDatabase,
    databases: tuple[Database, Database],
    handler: Scripted,
    admin: psycopg.AsyncConnection,
) -> None:
    """Review Focus 2: A's lease ran out and B took the job over; A's late record is refused, B's kept.

    Both while B still holds the job (its row ``running`` under B's token) and after B finished it.
    """
    zombie_metrics, live_metrics = CollectorRegistry(), CollectorRegistry()
    zombie = runner_of(probe, handler, zombie_metrics)
    live = runner_of(bind(databases[1]), handler, live_metrics)
    await enqueue(probe, "k1", "k2")
    stale = {job.key: job for job in await zombie.claim_now()}
    await run(admin, EXPIRED)
    handler.will("k1", Park("x"), Done())
    handler.will("k2", Done(), Park("x"))
    taken = {job.key: job for job in await live.claim_now()}
    with capture_logs() as logs:
        await zombie.handle(stale["k1"])  # B holds k1 and has not recorded yet
    status, attempts, _, parked, lease_until, lease_token, _ = await state(admin, "k1")
    assert (status, attempts, parked, lease_token) == ("running", 2, None, taken["k1"].lease_token)
    assert lease_until is not None
    await live.handle(taken["k1"])
    await live.handle(taken["k2"])
    with capture_logs() as later:
        await zombie.handle(stale["k2"])  # B finished k2 already
    for key in ("k1", "k2"):
        assert (await state(admin, key))[:4] == ("done", 2, None, None)
    lost = {"event": "job_lease_lost", "kind": EXPORT, "log_level": "warning"}
    assert [*logs, *later] == [lost, lost]
    assert zombie_metrics.get_sample_value("ledger_job_lease_lost_total", {"kind": EXPORT}) == 2
    assert finished(zombie_metrics, "parked") is None
    assert finished(live_metrics, "done") == 2


async def test_two_runners_never_run_one_job_twice(
    probe: BoundDatabase, databases: tuple[Database, Database], handler: Scripted
) -> None:
    keys = [f"k{index:02}" for index in range(20)]
    await enqueue(probe, *keys)
    first = runner_of(probe, handler, CollectorRegistry())
    second = runner_of(bind(databases[1]), handler, CollectorRegistry())
    total = 0
    while total < len(keys):
        total += sum(await asyncio.gather(first.run_due(), second.run_due()))
    assert total == len(keys)
    assert Counter(job.key for job in handler.seen) == dict.fromkeys(keys, 1)


async def test_a_handler_acts_as_its_job(
    probe: BoundDatabase, registry: CollectorRegistry, admin: psycopg.AsyncConnection
) -> None:
    async def auditing(job: Job) -> JobOutcome:
        async with probe.unit(job.scope) as uow:
            uow.audit(action="probe.export", subject=job.key)
        return Done()

    runner = JobRunner(database=probe, kinds={EXPORT: (DECLS[EXPORT], auditing)}, registry=registry)
    await enqueue(probe, "k1")
    assert await runner.run_due() == 1
    cursor = await admin.execute("SELECT actor, call_id, organization_id FROM probe.audit_events")
    assert await cursor.fetchall() == [("system:job:probe.export", "probe.export:k1", A)]


async def test_a_job_runs_in_the_trace_that_enqueued_it(
    probe: BoundDatabase,
    handler: Scripted,
    registry: CollectorRegistry,
    tracing: Tracing,
    exporter: InMemorySpanExporter,
) -> None:
    with tracing.tracer.start_as_current_span("request") as request:
        await enqueue(probe, "k1")
    handler.will("k1", UnavailableError())
    assert await runner_of(probe, handler, registry, tracing).run_due() == 1
    (job,) = handler.seen
    assert job.traceparent is not None
    _, span = exporter.get_finished_spans()
    assert span.name == f"job {EXPORT}"
    assert span.context is not None
    assert span.context.trace_id == request.get_span_context().trace_id
    assert span.status.status_code is trace.StatusCode.ERROR


@final
class Holding:
    """A handler that holds every job until released, and counts how many it holds at once."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.holding = 0
        self.most = 0

    async def __call__(self, job: Job) -> JobOutcome:
        del job
        self.holding += 1
        self.most = max(self.most, self.holding)
        try:
            await self.release.wait()
        finally:
            self.holding -= 1
        return Done()


@pytest.fixture
async def running(
    probe: BoundDatabase, registry: CollectorRegistry
) -> AsyncGenerator[tuple[asyncio.Task[object], Holding]]:
    holding = Holding()
    runner = JobRunner(database=probe, kinds={POLL: (DECLS[POLL], holding)}, registry=registry, poll=0.02)
    task: asyncio.Task[object] = asyncio.create_task(runner.run())
    yield task, holding
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_run_never_holds_more_jobs_of_a_kind_than_its_concurrency(
    probe: BoundDatabase, running: tuple[asyncio.Task[object], Holding], admin: psycopg.AsyncConnection
) -> None:
    task, holding = running
    await enqueue(probe, *(f"k{index}" for index in range(5)), kind=POLL)

    async def two_held() -> bool:
        return holding.holding == 2

    await waiting.eventually(two_held)
    cursor = await admin.execute("SELECT count(*) FROM probe.jobs WHERE status = 'running'")
    assert await cursor.fetchall() == [(2,)]  # claimed only for the free slots
    holding.release.set()

    async def all_done() -> bool:
        done = await admin.execute("SELECT count(*) FROM probe.jobs WHERE status = 'done'")
        return await done.fetchall() == [(5,)]

    await waiting.eventually(all_done)
    assert holding.most == 2
    assert not task.done()
