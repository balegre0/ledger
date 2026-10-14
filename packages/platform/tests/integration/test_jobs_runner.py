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
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import event
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ledger_kernel.errors import ConflictError, InvalidInputError, UnavailableError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import JobDecl
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database, DatabaseLimits
from ledger_platform.data.queue import Backoff, release
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.jobs import Done, Job, Park, RunAgain
from ledger_platform.jobs.outcomes import JobOutcome
from ledger_platform.jobs.runner import JobRunner
from ledger_testing import waiting
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.jobs.outcomes import JobHandler
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_testing.postgres import WorkerDatabase

A, A1 = OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7())
TENANT: Final = Tenant(A, A1, Mode.TEST)
EXPORT: Final = "probe.export"
POLL: Final = "probe.poll"
SLOW: Final = "probe.slow"
LEASE: Final = timedelta(seconds=60)
DECLS: Final = {
    EXPORT: JobDecl(kind=EXPORT, lease=LEASE, max_attempts=3, deadline=timedelta(hours=1)),
    POLL: JobDecl(kind=POLL, lease=LEASE, max_attempts=5, concurrency=2),
    SLOW: JobDecl(kind=SLOW, lease=timedelta(seconds=1)),
}
STATE: Final = """
    SELECT status, attempts, last_error_code, parked_reason, lease_until, lease_token, run_at - updated_at
    FROM probe.jobs WHERE key = %s
"""
DUE: Final = "UPDATE probe.jobs SET run_at = now() - interval '1 second' WHERE status = 'ready'"
EXPIRED: Final = "UPDATE probe.jobs SET lease_until = now() - interval '1 hour' WHERE status = 'running'"
"""Long enough ago that any backoff since has passed too: the job is due again at once."""
JUST_EXPIRED: Final = (
    "UPDATE probe.jobs SET lease_until = now() - interval '1 millisecond' WHERE status = 'running'"
)
UNLEASED: Final = (None, None)

_LEASE: Final = TypeAdapter(tuple[AwareDatetime, uuid.UUID])

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


async def test_a_job_polled_past_its_deadline_is_dead(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    await enqueue(probe, "polling")

    def beyond(job: Job) -> JobOutcome:
        assert job.deadline_at is not None
        return RunAgain(job.deadline_at + timedelta(seconds=1))

    handler.will("polling", beyond)
    assert await runner.run_due() == 1
    assert (await state(admin, "polling"))[:3] == ("dead", 1, "deadline_passed")
    assert finished(registry, "dead") == 1


async def test_a_retry_that_would_land_past_the_deadline_is_dead(
    probe: BoundDatabase, handler: Scripted, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """I1: the deadline is an hour away and the backoff two: retrying would only run past it."""
    kinds = {EXPORT: (DECLS[EXPORT], handler)}
    late = Backoff(steps=(timedelta(hours=2),), then=timedelta(hours=2))
    runner = JobRunner(database=probe, kinds=kinds, registry=registry, backoff=late)
    await enqueue(probe, "k1")
    handler.will("k1", UnavailableError())
    assert await runner.run_due() == 1
    assert (await state(admin, "k1"))[:3] == ("dead", 1, "deadline_passed")
    assert finished(registry, "dead") == 1


async def test_no_handler_runs_past_its_deadline(
    runner: JobRunner,
    probe: BoundDatabase,
    handler: Scripted,
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    """I1: a due job, and one whose worker died, are made dead once their deadline has passed, unclaimed."""
    await enqueue(probe, "due", "abandoned")
    await run(admin, "UPDATE probe.jobs SET run_at = now() + interval '1 hour' WHERE key = 'due'")
    (claimed,) = await runner.claim_now()
    assert claimed.key == "abandoned"
    await run(
        admin,
        "UPDATE probe.jobs SET run_at = now() - interval '1 s', deadline_at = now() - interval '1 s'",
    )
    await run(admin, EXPIRED)
    with capture_logs() as logs:
        assert await runner.claim_now() == []
    assert (await state(admin, "due"))[:6] == ("dead", 0, "deadline_passed", None, *UNLEASED)
    assert (await state(admin, "abandoned"))[:6] == ("dead", 1, "deadline_passed", None, *UNLEASED)
    assert handler.seen == []
    assert logs == [{"event": "job_deadline_passed", "kind": EXPORT, "jobs": 2, "log_level": "warning"}]
    assert finished(registry, "dead") == 2


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
    handler.will("k1", Done())  # the zombie never runs either: its first renewal finds B's token
    handler.will("k2", Done())
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
    for key in ("k1", "k2"):  # the code says why the second attempt came: the first one's worker was lost
        assert (await state(admin, key))[:4] == ("done", 2, "worker_lost", None)
    lost = {"event": "job_lease_lost", "kind": EXPORT, "log_level": "warning"}
    assert [*logs, *later] == [lost, lost]
    assert zombie_metrics.get_sample_value("ledger_job_lease_lost_total", {"kind": EXPORT}) == 2
    assert finished(zombie_metrics, "parked") is None
    assert finished(live_metrics, "done") == 2
    assert [job.lease_token for job in handler.seen] == [taken["k1"].lease_token, taken["k2"].lease_token]


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


@final
class Blocking:
    """A handler that runs until released, and says whether it was cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def __call__(self, job: Job) -> JobOutcome:
        del job
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return Done()


async def lease_of(admin: psycopg.AsyncConnection) -> tuple[datetime, uuid.UUID]:
    cursor = await admin.execute("SELECT lease_until, lease_token FROM probe.jobs")
    (row,) = await cursor.fetchall()
    return _LEASE.validate_python(tuple(row))


async def test_a_handler_that_outlives_its_lease_keeps_it(
    probe: BoundDatabase,
    databases: tuple[Database, Database],
    admin: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> None:
    """M1: the runner renews the lease while the handler runs; no one claims the job meanwhile."""
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry)
    await enqueue(probe, "k1", kind=SLOW)
    (job,) = await runner.claim_now()
    first, token = await lease_of(admin)
    handling = asyncio.create_task(runner.handle(job))

    async def renewed_past_the_first_lease() -> bool:
        cursor = await admin.execute(
            "SELECT now() > %s AND lease_until - now() > interval '0.5 seconds' FROM probe.jobs", (first,)
        )
        return await cursor.fetchall() == [(True,)]

    await waiting.eventually(renewed_past_the_first_lease)
    assert (await lease_of(admin))[1] == token
    other = JobRunner(database=bind(databases[1]), kinds={SLOW: (DECLS[SLOW], Scripted())})
    assert await other.claim_now() == []
    blocking.release.set()
    await handling
    assert (await state(admin, "k1"))[:2] == ("done", 1)
    assert not blocking.cancelled


async def test_a_handler_whose_lease_was_taken_is_cancelled(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """M1: the renewal finds another token: the handler is cancelled and nothing is recorded."""
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry)
    await enqueue(probe, "k1", kind=SLOW)
    (job,) = await runner.claim_now()
    with capture_logs() as logs:
        handling = asyncio.create_task(runner.handle(job))
        await blocking.started.wait()
        await run(admin, "UPDATE probe.jobs SET lease_token = gen_random_uuid()")
        await asyncio.wait_for(handling, timeout=5)
    assert blocking.cancelled
    _, stolen = await lease_of(admin)
    assert stolen != job.lease_token
    assert (await state(admin, "k1"))[:2] == ("running", 1)
    assert logs == [{"event": "job_lease_lost", "kind": SLOW, "log_level": "warning"}]
    assert registry.get_sample_value("ledger_job_lease_lost_total", {"kind": SLOW}) == 1


@pytest.mark.parametrize("started", [True, False], ids=["while-it-runs", "before-it-starts"])
async def test_a_lease_that_cannot_be_renewed_stops_the_handler(
    chassis: WorkerDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry, *, started: bool
) -> None:
    """M1, N1: a renewal that fails (a lock it may not wait for) cannot vouch for the lease.

    While the handler runs, it is cancelled; before it starts, it never starts.
    """
    limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=100))
    database = Database.create(app_url(chassis), pool_size=2, limits=limits)
    try:
        probe = bind(database)
        blocking = Blocking()
        runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry)
        await enqueue(probe, "k1", kind=SLOW)
        (job,) = await runner.claim_now()
        async with await psycopg.AsyncConnection.connect(chassis.admin_url()) as locking:
            with capture_logs() as logs:
                handling = asyncio.create_task(runner.handle(job)) if started else None
                if handling is not None:
                    await blocking.started.wait()
                await locking.execute("SELECT 1 FROM probe.jobs FOR UPDATE")
                await asyncio.wait_for(handling or runner.handle(job), timeout=5)
            await locking.rollback()
    finally:
        await database.aclose()
    assert blocking.cancelled is started
    assert blocking.started.is_set() is started
    assert [log["event"] for log in logs] == ["job_lease_renewal_failed", "job_lease_lost"]
    assert (await state(admin, "k1"))[:2] == ("running", 1)


async def test_a_claim_that_fails_is_retried_and_spares_the_jobs_in_flight(
    chassis: WorkerDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """M6: a claim the database refuses (a lock it may not wait for) is logged and retried; run() goes on."""
    limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=100))
    database = Database.create(app_url(chassis), pool_size=2, limits=limits)
    blocking = Blocking()
    probe = bind(database)
    runner = JobRunner(
        database=probe, kinds={EXPORT: (DECLS[EXPORT], blocking)}, registry=registry, poll=0.02
    )
    await enqueue(probe, "k1")
    try:
        with capture_logs() as logs:
            task = asyncio.create_task(runner.run())
            await blocking.started.wait()
            async with await psycopg.AsyncConnection.connect(chassis.admin_url()) as locking:
                await locking.execute("LOCK TABLE probe.jobs IN ACCESS EXCLUSIVE MODE")

                async def failed_twice() -> bool:
                    return [log["event"] for log in logs].count("job_claim_failed") >= 2

                await waiting.eventually(failed_twice)
                await locking.rollback()
            assert not blocking.cancelled
            blocking.release.set()

            async def done() -> bool:
                cursor = await admin.execute("SELECT status FROM probe.jobs")
                return await cursor.fetchall() == [("done",)]

            await waiting.eventually(done)
            assert not task.done()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        await database.aclose()


async def test_run_claims_again_at_once_while_a_kind_has_free_slots_and_due_jobs(
    probe: BoundDatabase, handler: Scripted, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """M7: a claim that filled its batch is followed by another, not by a poll's wait (here a minute)."""
    runner = JobRunner(
        database=probe, kinds={EXPORT: (DECLS[EXPORT], handler)}, registry=registry, batch=2, poll=60
    )
    await enqueue(probe, *(f"k{index}" for index in range(6)))
    task = asyncio.create_task(runner.run())

    async def all_done() -> bool:
        cursor = await admin.execute("SELECT count(*) FROM probe.jobs WHERE status = 'done'")
        return await cursor.fetchall() == [(6,)]

    try:
        await waiting.eventually(all_done)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_a_runner_stopped_mid_job_stops_the_handler_and_records_nothing(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """A cancellation from outside (the worker stops) is no lost lease: it propagates, the lease runs out."""
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry)
    await enqueue(probe, "k1", kind=SLOW)
    (job,) = await runner.claim_now()
    handling = asyncio.create_task(runner.handle(job))
    await blocking.started.wait()
    handling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handling
    assert blocking.cancelled
    assert (await state(admin, "k1"))[:2] == ("running", 1)
    assert registry.get_sample_value("ledger_job_lease_lost_total", {"kind": SLOW}) is None


async def test_a_stopped_runner_lets_its_job_finish(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry, poll=0.02)
    await enqueue(probe, "k1", kind=SLOW)
    running = asyncio.create_task(runner.run())
    await blocking.started.wait()
    runner.stop()
    blocking.release.set()
    async with asyncio.timeout(5):
        await runner.drained.wait()
    assert (await state(admin, "k1"))[:2] == ("done", 1)
    assert not blocking.cancelled
    assert not running.done()  # a stopped worker waits to be cancelled: it never returns
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_a_job_claimed_while_the_runner_stops_is_given_back_unstarted(
    chassis: WorkerDatabase, probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """``stop()`` lands while a claim waits (on a lock the test holds): the job it claims never starts."""
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry, poll=0.02)
    await enqueue(probe, "k1", kind=SLOW)
    async with admin.transaction():
        await admin.execute("LOCK TABLE probe.jobs IN ACCESS EXCLUSIVE MODE")
        running = asyncio.create_task(runner.run())

        async def claim_waits() -> bool:
            return await waiting.waits_for_a_lock(app_url(chassis))

        await waiting.eventually(claim_waits)
        runner.stop()
    async with asyncio.timeout(5):
        await runner.drained.wait()
    assert not blocking.started.is_set()
    found = await state(admin, "k1")
    assert (found[:2], found[4:6]) == (("ready", 0), UNLEASED)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


async def test_a_stopped_runner_starts_no_job_and_gives_it_back_unspent(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """A job claimed before ``stop()`` and not started yet: refused at its start, its attempt refunded."""
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry)
    await enqueue(probe, "k1", kind=SLOW)
    (job,) = await runner.claim_now()
    runner.stop()
    await runner.handle(job)
    assert not blocking.started.is_set()
    found = await state(admin, "k1")
    assert (found[:2], found[4:6]) == (("ready", 0), UNLEASED)
    assert finished(registry, "dead", SLOW) is None
    assert registry.get_sample_value("ledger_job_lease_lost_total", {"kind": SLOW}) is None


async def test_a_late_give_back_never_touches_a_job_another_runner_took_over(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """The release is fenced by the lease token: a replica that reclaimed the job keeps it."""
    runner = runner_of(probe, Scripted(), registry)
    await enqueue(probe, "k1")
    (job,) = await runner.claim_now()
    await admin.execute(
        "UPDATE probe.jobs SET lease_token = gen_random_uuid(), attempts = attempts + 1 WHERE key = 'k1'"
    )
    taken = await state(admin, "k1")
    async with probe.unit(None) as unit:
        await release(
            unit.connection,
            probe.tables.jobs,
            keys=("kind", "key"),
            started=[],
            unstarted=[(job.kind, job.key, job.lease_token)],
        )
    assert await state(admin, "k1") == taken
    assert taken[:2] == ("running", 2)


async def test_a_runner_cancelled_mid_job_gives_the_job_back_with_its_attempt_spent(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    blocking = Blocking()
    runner = JobRunner(database=probe, kinds={SLOW: (DECLS[SLOW], blocking)}, registry=registry, poll=0.02)
    await enqueue(probe, "k1", kind=SLOW)
    running = asyncio.create_task(runner.run())
    await blocking.started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert blocking.cancelled
    found = await state(admin, "k1")
    assert (found[:2], found[4:6]) == (("ready", 1), UNLEASED)


async def test_a_job_whose_lease_ran_out_waits_out_its_backoff_before_another_claim(
    runner: JobRunner, probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """I4: its worker died just now; the job is ready again after the backoff, like a failed run."""
    await enqueue(probe, "k1")
    await runner.claim_now()
    await run(admin, JUST_EXPIRED)
    with capture_logs() as logs:
        assert await runner.claim_now() == []
    assert logs == [{"event": "job_worker_recovered", "kind": EXPORT, "jobs": 1, "log_level": "warning"}]
    assert registry.get_sample_value("ledger_job_worker_recovered_total", {"kind": EXPORT}) == 1
    assert (await state(admin, "k1"))[:6] == ("ready", 1, "worker_lost", None, *UNLEASED)
    cursor = await admin.execute("SELECT run_at > now() FROM probe.jobs")
    assert await cursor.fetchall() == [(True,)]
    await run(admin, DUE)
    (again,) = await runner.claim_now()
    assert again.attempts == 2


async def test_a_job_whose_lease_was_taken_before_its_handler_started_is_not_run(
    runner: JobRunner, probe: BoundDatabase, handler: Scripted, admin: psycopg.AsyncConnection
) -> None:
    """N1: the first renewal comes before the handler: a taken lease never runs it."""
    await enqueue(probe, "k1")
    (job,) = await runner.claim_now()
    await run(admin, "UPDATE probe.jobs SET lease_token = gen_random_uuid()")
    with capture_logs() as logs:
        await runner.handle(job)
    assert handler.seen == []
    assert logs == [{"event": "job_lease_lost", "kind": EXPORT, "log_level": "warning"}]


async def test_a_record_after_the_job_was_taken_over_while_it_ran_changes_nothing(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, registry: CollectorRegistry
) -> None:
    """Review Focus 2: taken over between two renewals, the run's outcome is refused when it is recorded."""

    async def taken_meanwhile(job: Job) -> JobOutcome:
        del job
        await run(admin, "UPDATE probe.jobs SET lease_token = gen_random_uuid()")
        return Park("x")

    runner = JobRunner(database=probe, kinds={EXPORT: (DECLS[EXPORT], taken_meanwhile)}, registry=registry)
    await enqueue(probe, "k1")
    (job,) = await runner.claim_now()
    with capture_logs() as logs:
        await runner.handle(job)
    assert logs == [{"event": "job_lease_lost", "kind": EXPORT, "log_level": "warning"}]
    assert (await state(admin, "k1"))[:4] == ("running", 1, None, None)
    assert finished(registry, "parked") is None


async def test_a_kind_at_its_concurrency_is_not_claimed_while_the_others_are(
    probe: BoundDatabase, databases: tuple[Database, Database], admin: psycopg.AsyncConnection
) -> None:
    """``run`` claims a kind for its free slots only: at its concurrency it is skipped, the next is not."""
    blocking = Blocking()
    single = JobDecl(kind=EXPORT, lease=LEASE, max_attempts=3, concurrency=1)
    kinds: dict[str, tuple[JobDecl, JobHandler]] = {
        EXPORT: (single, blocking),
        POLL: (DECLS[POLL], Scripted()),
    }
    runner = JobRunner(database=probe, kinds=kinds, poll=0.02)
    claims: list[str] = []

    def heard(*arguments: object) -> None:
        if "WITH chosen" in str(arguments[2]):  # the statement of a claim
            claims.append("claim")

    event.listen(databases[0].engine.sync_engine, "before_cursor_execute", heard)
    await enqueue(probe, "k1", "k2")
    task = asyncio.create_task(runner.run())
    try:
        await blocking.started.wait()
        seen = len(claims)

        async def a_later_pass_claimed() -> bool:  # every pass claims POLL; EXPORT, full, is skipped first
            return len(claims) >= seen + 2

        await waiting.eventually(a_later_pass_claimed)
        assert [(await state(admin, key))[0] for key in ("k1", "k2")] == ["running", "ready"]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
