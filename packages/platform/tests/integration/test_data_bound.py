"""``BoundDatabase`` on real PostgreSQL: role, scope, records, and what a pooled connection keeps (§5.3).

The pool has one connection, so every unit of a test runs on the same PostgreSQL session (each test asserts it
through ``pg_backend_pid``): whatever a unit left behind, the next one would see.
"""

import asyncio
import contextlib
import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Callable

import psycopg
import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from structlog.testing import capture_logs
lazy from sqlalchemy import Executable

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.data.audit import Actor, MissingActorError, acting_as
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.codec import canonical_json, encode
from ledger_platform.data.database import AbortedTransactionError, ClosedUnitOfWorkError, Database
from ledger_platform.data.roles import InvalidSchemaNameError
from ledger_platform.data.unit_of_work import (
    EventTooLargeError,
    JobPolicy,
    MissingAuditScopeError,
    UndeclaredEventTypeError,
    UndeclaredJobKindError,
    open_unit,
)
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.postgres import WorkerDatabase

A, B = OrganizationId(uuid.uuid7()), OrganizationId(uuid.uuid7())
A1, A2, B1 = CompanyId(uuid.uuid7()), CompanyId(uuid.uuid7()), CompanyId(uuid.uuid7())
SUBJECT: Final = uuid.uuid7()
EXPORT: Final = "probe.export"
SWEEP: Final = "probe.sweep"
ACTOR: Final = Actor("user:2001", "req-1")
ACTED: Final = ("user:2001", "req-1")
"""``ACTOR`` as an audit row records it: ``actor``, ``call_id``."""
CLEAN: Final = ("ledger_app", "none", "", "", "")
"""A pooled session between units: the login itself, no role, no tenant setting."""
STATE: Final = text(
    """
    SELECT current_user, current_setting('role'),
           coalesce(current_setting('ledger.organization_id', true), ''),
           coalesce(current_setting('ledger.company_id', true), ''),
           coalesce(current_setting('ledger.mode', true), ''),
           pg_backend_pid()
    """
)
_SETTINGS: Final = text(
    """
    SELECT current_user,
           current_setting('ledger.organization_id', true),
           current_setting('ledger.company_id', true),
           current_setting('ledger.mode', true)
    """
)
_PID: Final = text("SELECT pg_backend_pid()")
DIVIDE_BY_ZERO: Final = text("SELECT 1 / 0")
_NUMBER: Final = TypeAdapter(int)
COUNTS: Final = """
    SELECT (SELECT count(*) FROM probe.outbox), (SELECT count(*) FROM probe.jobs),
           (SELECT count(*) FROM probe.audit_events)
"""
OUTBOX: Final = """
    SELECT type, version, source, organization_id, company_id, mode, subject, aggregate_version, occurred_at,
           traceparent, payload, attempts, unrouted_since
    FROM probe.outbox ORDER BY id
"""
JOBS: Final = """
    SELECT kind, key, organization_id, company_id, mode, status, run_at, lease_until, lease_token, attempts,
           max_attempts, deadline_at, parked_reason, payload, last_error_code, traceparent, created_at,
           updated_at
    FROM probe.jobs ORDER BY kind, key
"""
AUDIT: Final = """
    SELECT occurred_at, organization_id, company_id, mode, actor, call_id, action, subject_type, subject_id,
           before_hash, after_hash, detail
    FROM probe.audit_events ORDER BY action DESC
"""
FINISH: Final = """
    UPDATE probe.jobs SET status = %s, attempts = 4, lease_until = now(), lease_token = gen_random_uuid(),
                          parked_reason = 'x', last_error_code = 'boom'
"""
RESTARTED: Final = """
    SELECT status, attempts, payload, run_at, deadline_at, lease_until, lease_token, parked_reason,
           last_error_code, created_at, updated_at
    FROM probe.jobs
"""
PARK: Final = """
    UPDATE probe.jobs SET status = 'parked',
                          parked_reason = CASE WHEN key LIKE '%%unavailable' THEN 'signer_unavailable'
                                               ELSE 'certificate_missing' END
    WHERE key <> 'a-ready'
"""
KEY: Final = r"""
    INSERT INTO probe.idempotency_keys
        (organization_id, mode, principal, route, key, request_hash, response_body, created_at, expires_at)
    VALUES (%s, 'live', 'user:1', 'POST /v1/things', %s, '\x00', 'secret', now(), now() + %s::interval)
"""
KEYS: Final = text("SELECT key FROM probe.idempotency_keys ORDER BY key")
UPDATE_KEYS: Final = text("UPDATE probe.idempotency_keys SET expires_at = now()")
INSERT_KEY: Final = text(
    r"""
    INSERT INTO probe.idempotency_keys
        (organization_id, mode, principal, route, key, request_hash, created_at, expires_at)
    VALUES (gen_random_uuid(), 'live', 'p', 'r', 'k', '\x00', now(), now())
    """
)
OUTBOX_COUNT: Final = text("SELECT count(*) FROM probe.outbox")
PURGE: Final = text("DELETE FROM probe.idempotency_keys WHERE expires_at < now()")
"""What the purge runs: a plain ``DELETE``, never ``RETURNING`` the stored responses (ruling 1)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.item.renamed"
    VERSION: ClassVar[int] = 1
    name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Archived(Event):
    TYPE: ClassVar[str] = "probe.item.archived"
    VERSION: ClassVar[int] = 1


@final
class RecordedSignals:
    """``Signals`` that remember what they were told, in order."""

    def __init__(self) -> None:
        self.sent: list[Channel] = []

    def signal(self, channel: Channel) -> None:
        self.sent.append(channel)


@pytest.fixture
async def database(chassis: WorkerDatabase) -> AsyncGenerator[Database]:
    """One connection: every unit of a test reuses it (``chassis`` empties the schemas after the test)."""
    opened = Database.create(app_url(chassis), pool_size=1)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session for a test's setup and checks (it bypasses row-level security)."""
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture
def signals() -> RecordedSignals:
    return RecordedSignals()


def bind(
    database: Database, signals: RecordedSignals, clock: ManualClock, schema: str = "probe"
) -> BoundDatabase:
    return BoundDatabase(
        database,
        schema=schema,
        source=schema,
        publishes=frozenset({Renamed.TYPE}),
        jobs={
            EXPORT: JobPolicy(max_attempts=5, deadline=timedelta(hours=1)),
            SWEEP: JobPolicy(max_attempts=3, deadline=None),
        },
        signals=signals,
        clock=clock,
    )


@pytest.fixture
def probe(database: Database, signals: RecordedSignals, clock: ManualClock) -> BoundDatabase:
    return bind(database, signals, clock)


async def rows(
    admin: psycopg.AsyncConnection, query: LiteralString, *params: object
) -> list[tuple[object, ...]]:
    cursor = await admin.execute(query, params)
    return [tuple(row) for row in await cursor.fetchall()]


async def pooled(database: Database) -> tuple[object, ...]:
    """The pooled session's role and settings as the next unit would find them, and its backend's pid."""
    async with database.engine.connect() as connection:
        return tuple((await connection.execute(STATE)).one())


async def pid_of(unit: UnitOfWork) -> int:
    return _NUMBER.validate_python((await unit.connection.execute(_PID)).scalar_one())


async def count(unit: UnitOfWork, statement: Executable | None = None) -> int:
    """The idempotency keys ``unit`` sees (or the count ``statement`` selects)."""
    keys = unit.tables.idempotency_keys
    query = statement if statement is not None else select(func.count()).select_from(keys)
    return _NUMBER.validate_python((await unit.connection.execute(query)).scalar_one())


def add_key(unit: UnitOfWork, organization: uuid.UUID, mode: str, key: str) -> Executable:
    return insert(unit.tables.idempotency_keys).values(
        organization_id=organization,
        mode=mode,
        principal="user:1",
        route="POST /v1/things",
        key=key,
        request_hash=b"\x00",
        created_at=unit.now,
        expires_at=unit.now + timedelta(days=1),
    )


def tenant(organization: OrganizationId, company: CompanyId, mode: Mode | None = Mode.TEST) -> Tenant:
    return Tenant(organization, company, mode)


async def test_a_unit_runs_in_its_modules_role_with_its_scope(probe: BoundDatabase) -> None:
    assert probe.schema == "probe"
    assert probe.tables.outbox.schema == "probe"
    async with probe.unit(tenant(A, A1, Mode.LIVE)) as uow:
        settings = tuple((await uow.connection.execute(_SETTINGS)).one())
        assert uow.scope == tenant(A, A1, Mode.LIVE)
    assert settings == ("probe_rw", str(A), str(A1), "live")
    async with probe.unit(OrganizationScope(A, None)) as uow:
        settings = tuple((await uow.connection.execute(_SETTINGS)).one())
    assert settings == ("probe_rw", str(A), "", "")


def test_a_schema_name_that_cannot_be_a_role_is_refused(
    database: Database, signals: RecordedSignals, clock: ManualClock
) -> None:
    with pytest.raises(InvalidSchemaNameError):
        bind(database, signals, clock, schema="Probe; DROP")


async def test_one_pooled_connection_never_carries_a_scope_or_a_role_into_the_next_unit(
    database: Database, probe: BoundDatabase, signals: RecordedSignals, clock: ManualClock
) -> None:
    """Review Focus 1: A in test writes; no scope, B, A live, A mode-less see nothing; A in test sees it.

    Then another module's unit is refused the table, and the pooled session is the login's again.
    """
    pids: set[int] = set()
    async with probe.unit(tenant(A, A1)) as uow:
        await uow.connection.execute(add_key(uow, A, "test", "k1"))
        pids.add(await pid_of(uow))
    seen: list[int] = []
    for scope in (None, tenant(B, B1), tenant(A, A1, Mode.LIVE), tenant(A, A1, None), tenant(A, A1)):
        async with probe.unit(scope) as uow:
            seen.append(await count(uow))
            pids.add(await pid_of(uow))
    assert seen == [0, 0, 0, 0, 1]
    other = bind(database, signals, clock, schema="other")

    async def reaching_into_probe() -> None:
        async with other.unit(tenant(A, A1)) as uow:
            pids.add(await pid_of(uow))
            await count(uow, select(func.count()).select_from(probe.tables.idempotency_keys))

    with pytest.raises(ProgrammingError, match="permission denied for schema probe"):
        await reaching_into_probe()
    *state, pid = await pooled(database)
    assert tuple(state) == CLEAN
    assert pids == {pid}


async def swallowing_a_failure(
    probe: BoundDatabase, scope: Tenant | None, *, publishing: bool = False
) -> None:
    """A unit whose statement fails and whose block goes on as if it had not (a defect of the caller)."""
    async with probe.unit(scope) as uow:
        if publishing:
            uow.publish(Renamed(scope=None, subject=None, aggregate_version=None, name="x"))
        with contextlib.suppress(DBAPIError):
            await uow.connection.execute(DIVIDE_BY_ZERO)


async def test_a_pooled_connection_is_clean_after_every_way_a_unit_can_end(
    database: Database, probe: BoundDatabase
) -> None:
    """Ruling 3: after a commit, a raise, a failed statement, an aborted transaction and a cancelled task."""
    states: list[tuple[object, ...]] = []
    async with probe.unit(tenant(A, A1)) as uow:
        pass
    states.append(await pooled(database))
    with pytest.raises(LookupError):
        async with probe.unit(tenant(A, A1)) as uow:
            raise LookupError
    states.append(await pooled(database))
    with pytest.raises(DBAPIError):
        async with probe.unit(tenant(A, A1)) as uow:
            await uow.connection.execute(DIVIDE_BY_ZERO)
    states.append(await pooled(database))
    with pytest.raises(AbortedTransactionError):
        await swallowing_a_failure(probe, tenant(A, A1))
    states.append(await pooled(database))
    entered = asyncio.Event()

    async def stuck_between_statements() -> None:
        async with probe.unit(tenant(B, B1, Mode.LIVE)) as uow:
            await uow.connection.execute(_PID)
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(stuck_between_statements())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    states.append(await pooled(database))
    assert [state[:-1] for state in states] == [CLEAN] * 5
    assert len({state[-1] for state in states}) == 1


async def test_a_unit_cancelled_inside_a_statement_leaves_nothing_to_the_next(
    database: Database, probe: BoundDatabase
) -> None:
    """A cancellation while PostgreSQL works: the session is rolled back or replaced, never handed on."""
    entered = asyncio.Event()

    async def sleeping() -> None:
        async with probe.unit(tenant(B, B1, Mode.LIVE)) as uow:
            entered.set()
            await uow.connection.execute(text("SELECT pg_sleep(30)"))

    task = asyncio.create_task(sleeping())
    await entered.wait()
    await asyncio.sleep(0.2)  # let the statement reach the server: the cancellation must interrupt it
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    assert (await pooled(database))[:-1] == CLEAN
    async with probe.unit(None) as uow:
        assert await count(uow) == 0


async def test_a_rolled_back_unit_leaves_nothing_and_signals_nothing(
    probe: BoundDatabase, signals: RecordedSignals, admin: psycopg.AsyncConnection
) -> None:
    ran: list[str] = []

    async def recording_then_failing() -> None:
        async with probe.unit(tenant(A, A1)) as uow:
            uow.publish(Renamed(scope=tenant(A, A1), subject=SUBJECT, aggregate_version=1, name="x"))
            uow.jobs.enqueue(EXPORT, "k1")
            uow.audit(action="probe.rename", subject=SUBJECT)
            uow.after_commit(lambda: ran.append("after"))
            raise LookupError

    with acting_as(ACTOR), pytest.raises(LookupError):
        await recording_then_failing()
    assert await rows(admin, COUNTS) == [(0, 0, 0)]
    assert signals.sent == []
    assert ran == []
    assert open_unit() is None


async def test_publish_writes_the_envelope_with_the_units_time_and_signals_after_commit(
    probe: BoundDatabase, signals: RecordedSignals, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    scope = tenant(A, A1)
    async with probe.unit(scope) as uow:
        uow.publish(Renamed(scope=scope, subject=SUBJECT, aggregate_version=3, name="x"))
        uow.publish(Renamed(scope=OrganizationScope(A, None), subject=None, aggregate_version=None, name="y"))
        assert signals.sent == []
    assert signals.sent == ["ledger_outbox"]  # one signal per commit, however many events it wrote
    renamed, version = Renamed.TYPE, Renamed.VERSION
    assert await rows(admin, OUTBOX) == [
        (renamed, version, "probe", A, A1, "test", SUBJECT, 3, clock(), None, {"name": "x"}, 0, None),
        (renamed, version, "probe", A, None, None, None, None, clock(), None, {"name": "y"}, 0, None),
    ]


async def test_a_unit_that_publishes_nothing_signals_nothing(
    probe: BoundDatabase, signals: RecordedSignals
) -> None:
    async with probe.unit(tenant(A, A1)) as uow:
        uow.jobs.enqueue(EXPORT, "k1")
    assert signals.sent == []


async def test_publish_carries_the_current_traceparent(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("rename") as span:
        async with probe.unit(None) as uow:
            uow.publish(Renamed(scope=None, subject=None, aggregate_version=None, name="x"))
            uow.jobs.enqueue(EXPORT, "k1")
    context = span.get_span_context()
    expected = f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
    traceparents = "SELECT traceparent FROM probe.outbox UNION ALL SELECT traceparent FROM probe.jobs"
    assert await rows(admin, traceparents) == [(expected,), (expected,)]


async def test_an_undeclared_event_type_and_an_oversized_payload_are_refused_at_publish(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with probe.unit(None) as uow:
        with pytest.raises(UndeclaredEventTypeError, match=r"^probe publishes probe\.item\.archived"):
            uow.publish(Archived(scope=None, subject=None, aggregate_version=None))
        with pytest.raises(EventTooLargeError, match=r"above 65536$"):
            uow.publish(Renamed(scope=None, subject=None, aggregate_version=None, name="x" * 70_000))
        largest = 65_536 - len('{"name": ""}')
        uow.publish(Renamed(scope=None, subject=None, aggregate_version=None, name="x" * largest))
    assert await rows(admin, "SELECT octet_length(payload::text) FROM probe.outbox") == [(65_536,)]


async def test_recording_after_the_unit_ended_is_refused(probe: BoundDatabase) -> None:
    async with probe.unit(tenant(A, A1)) as uow:
        inside = uow.is_open
    assert (inside, uow.is_open) == (True, False)
    event = Renamed(scope=None, subject=None, aggregate_version=None, name="x")
    with acting_as(ACTOR):
        refused: tuple[Callable[[], None], ...] = (
            lambda: uow.publish(event),
            lambda: uow.jobs.enqueue(EXPORT, "k1"),
            lambda: uow.jobs.wake(EXPORT, "k1"),
            lambda: uow.jobs.wake_all(EXPORT, A1, "certificate_missing"),
            lambda: uow.audit(action="probe.rename", subject=SUBJECT),
            lambda: uow.after_commit(lambda: None),
        )
        for record in refused:
            with pytest.raises(ClosedUnitOfWorkError):
                record()


async def test_enqueue_writes_a_ready_job_with_its_kinds_policy(
    probe: BoundDatabase, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    later = clock() + timedelta(minutes=5)
    async with probe.unit(tenant(A, A1, Mode.LIVE)) as uow:
        uow.jobs.enqueue(EXPORT, "k1", payload={"amount": Decimal("1.50")})
        uow.jobs.enqueue(SWEEP, "k2", run_at=later)
    now = clock()
    deadline = now + timedelta(hours=1)
    unleased = (None, None, 0)
    assert await rows(admin, JOBS) == [
        (
            EXPORT,
            "k1",
            A,
            A1,
            "live",
            "ready",
            now,
            *unleased,
            5,
            deadline,
            None,
            {"amount": "1.50"},
            None,
            None,
            now,
            now,
        ),
        (SWEEP, "k2", A, A1, "live", "ready", later, *unleased, 3, None, None, {}, None, None, now, now),
    ]


async def test_enqueue_ignores_a_live_job_and_restarts_a_finished_one(
    probe: BoundDatabase, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    first = clock()
    async with probe.unit(None) as uow:
        uow.jobs.enqueue(EXPORT, "k1", payload={"n": 1})
        uow.jobs.enqueue(EXPORT, "k1", payload={"n": 2})
    assert await rows(admin, "SELECT status, payload FROM probe.jobs") == [("ready", {"n": 1})]
    for finished in ("done", "dead"):
        await admin.execute(FINISH, (finished,))
        moment = clock.advance(timedelta(minutes=1))
        async with probe.unit(None) as uow:
            uow.jobs.enqueue(EXPORT, "k1", payload={"n": finished})
        assert await rows(admin, RESTARTED) == [
            (
                "ready",
                0,
                {"n": finished},
                moment,
                moment + timedelta(hours=1),
                None,
                None,
                None,
                None,
                first,
                moment,
            )
        ]
    for live in ("running", "parked"):
        await admin.execute("UPDATE probe.jobs SET status = %s, payload = '{}'", (live,))
        async with probe.unit(None) as uow:
            uow.jobs.enqueue(EXPORT, "k1", payload={"n": 9})
        assert await rows(admin, "SELECT status, payload FROM probe.jobs") == [(live, {})]


async def test_an_undeclared_job_kind_is_refused(probe: BoundDatabase) -> None:
    async with probe.unit(None) as uow:
        undeclared: tuple[Callable[[], None], ...] = (
            lambda: uow.jobs.enqueue("probe.unknown", "k1"),
            lambda: uow.jobs.wake("probe.unknown", "k1"),
            lambda: uow.jobs.wake_all("probe.unknown", A1, "certificate_missing"),
        )
        for record in undeclared:
            with pytest.raises(UndeclaredJobKindError, match=r"^probe enqueues probe\.unknown"):
                record()


async def test_wake_and_wake_all_ready_only_the_parked_jobs_they_name(
    probe: BoundDatabase, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    async with probe.unit(tenant(A, A1)) as uow:
        for key in ("a-missing", "a-unavailable", "a-ready"):
            uow.jobs.enqueue(EXPORT, key)
        uow.jobs.enqueue(SWEEP, "a-other-kind")
    async with probe.unit(tenant(B, B1)) as uow:
        uow.jobs.enqueue(EXPORT, "b-missing")
        uow.jobs.enqueue(EXPORT, "b-later")
    await admin.execute(PARK)
    enqueued = clock()
    woken = clock.advance(timedelta(minutes=3))
    async with probe.unit(None) as uow:
        uow.jobs.wake_all(EXPORT, A1, "certificate_missing")
        uow.jobs.wake(EXPORT, "b-missing")
        uow.jobs.wake(EXPORT, "a-ready")  # not parked: left as it is
    assert await rows(
        admin, "SELECT kind, key, status, run_at, parked_reason FROM probe.jobs ORDER BY kind, key"
    ) == [
        (EXPORT, "a-missing", "ready", woken, None),
        (EXPORT, "a-ready", "ready", enqueued, None),
        (EXPORT, "a-unavailable", "parked", enqueued, "signer_unavailable"),
        (EXPORT, "b-later", "parked", enqueued, "certificate_missing"),
        (EXPORT, "b-missing", "ready", woken, None),
        (SWEEP, "a-other-kind", "parked", enqueued, "certificate_missing"),
    ]


async def test_audit_writes_who_did_what_with_hashes_never_data(
    probe: BoundDatabase, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    old, new = {"name": "old", "tax_id": "101850043"}, {"name": "new", "tax_id": "101850043"}
    with acting_as(ACTOR):
        async with probe.unit(tenant(A, A1, Mode.LIVE)) as uow:
            uow.audit(action="probe.rename", subject=SUBJECT, before=old, after=new, detail={"field": "name"})
        async with probe.unit(OrganizationScope(A, None)) as uow:
            uow.audit(action="probe.create", subject="item-7")
    before = hashlib.sha256(canonical_json(encode(old))).digest()
    after = hashlib.sha256(canonical_json(encode(new))).digest()
    assert await rows(admin, AUDIT) == [
        (
            clock(),
            A,
            A1,
            "live",
            *ACTED,
            "probe.rename",
            "probe",
            str(SUBJECT),
            before,
            after,
            {"field": "name"},
        ),
        (clock(), A, None, None, *ACTED, "probe.create", "probe", "item-7", None, None, {}),
    ]


async def test_an_audit_needs_an_acting_call_and_an_organization(probe: BoundDatabase) -> None:
    """Ruling 2: an audit row says who and in which organization; without either it is a defect, at once."""
    async with probe.unit(tenant(A, A1)) as uow:
        with pytest.raises(MissingActorError):
            uow.audit(action="probe.rename", subject=SUBJECT)
    with acting_as(ACTOR):
        async with probe.unit(None) as uow:
            with pytest.raises(MissingAuditScopeError, match="organization") as raised:
                uow.audit(action="probe.rename", subject=SUBJECT)
    assert raised.value.code == "audit_scope_missing"


async def seen_later() -> UnitOfWork | None:
    await asyncio.sleep(0.05)
    return open_unit()


async def test_the_open_unit_is_known_to_the_task_and_forgotten_after(probe: BoundDatabase) -> None:
    assert open_unit() is None
    async with probe.unit(None) as uow:
        assert open_unit() is uow
        inherited = asyncio.create_task(seen_later())
    assert open_unit() is None
    assert await inherited is None  # its copied context still holds the unit, which is closed


async def test_a_caught_statement_failure_never_commits(
    probe: BoundDatabase, signals: RecordedSignals, admin: psycopg.AsyncConnection
) -> None:
    with pytest.raises(AbortedTransactionError):
        await swallowing_a_failure(probe, None, publishing=True)
    assert await rows(admin, COUNTS) == [(0, 0, 0)]
    assert signals.sent == []


async def test_after_commit_callbacks_run_in_order_once_committed_whatever_one_raises(
    probe: BoundDatabase,
) -> None:
    ran: list[str] = []

    def failing() -> None:
        ran.append("failing")
        raise LookupError

    with capture_logs() as logs:
        async with probe.unit(None) as uow:
            uow.after_commit(lambda: ran.append("first"))
            uow.after_commit(failing)
            uow.after_commit(lambda: ran.append("last"))
            assert ran == []
    assert ran == ["first", "failing", "last"]
    assert [log["event"] for log in logs] == ["unit.after_commit_failed"]


async def test_a_purging_connection_sees_expired_keys_of_every_organization_only(
    database: Database, probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    """Decision 6 and ruling 1: the purge role, no tenant setting, expired keys only, no body read back."""
    for organization, key, expires in (
        (A, "a-old", "-1 day"),
        (B, "b-old", "-1 hour"),
        (B, "b-live", "1 day"),
    ):
        await admin.execute(KEY, (organization, key, expires))
    async with probe.unit(tenant(B, B1, Mode.LIVE)) as uow:  # the session's previous unit had settings
        assert await count(uow) == 2  # B's live keys, expired or not: the rw role does not purge
    async with probe.purging() as connection:
        settings = tuple((await connection.execute(_SETTINGS)).one())
        visible = tuple((await connection.execute(KEYS)).scalars())
        for refused in (UPDATE_KEYS, INSERT_KEY, OUTBOX_COUNT):
            with pytest.raises(ProgrammingError, match="permission denied"):
                async with connection.begin_nested():
                    await connection.execute(refused)
        deleted = await connection.execute(PURGE)
    assert settings == ("probe_purge", "", "", "")
    assert visible == ("a-old", "b-old")
    assert deleted.rowcount == 2
    assert await rows(admin, "SELECT key FROM probe.idempotency_keys") == [("b-live",)]
    assert (await pooled(database))[:-1] == CLEAN
    async with probe.unit(None) as uow:
        await uow.connection.execute(text("SET LOCAL ledger.purge = 'on'"))
        assert await count(uow) == 0
