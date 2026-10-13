"""``Idempotency.once`` on real PostgreSQL (§9.5, Review Focus 4): keys per organization, mode, caller, route.

The work of each test enqueues a job in the unit's own schema (``probe.jobs``): what a first use changes, and
what a replay must not change again. Units run in ``probe``'s role under row-level security, as a module's do.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Final, LiteralString
lazy from collections.abc import AsyncGenerator

import psycopg
import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ledger_kernel.access import Principal, SubjectId
from ledger_kernel.tenancy import Mode, OrganizationScope
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.http.idempotency import (
    MAX_STORED_BODY,
    Attempt,
    Idempotency,
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    OversizedStoredResponseError,
    ServerFailureResponseError,
    StoredResponse,
    Target,
    UnansweredIdempotencyKeyError,
    UnmodedIdempotencyScopeError,
    canonical_body,
    json_response,
)
from ledger_testing.database import app_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.postgres import WorkerDatabase

PROVIDER_ORGANIZATION: Final = "1001"
ORGANIZATION: Final = organization_of(PROVIDER_ORGANIZATION)
MARK: Final = "probe.mark"
CREATE: Final = "POST /v1/things"
KEY: Final = "key-1"
MARKS: Final = "SELECT count(*) FROM probe.jobs WHERE kind = 'probe.mark'"
KEYS: Final = "SELECT count(*) FROM probe.idempotency_keys"
_COUNT: Final = TypeAdapter(int)


class NewThing(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str


class Thing(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    name: str


class Silent:
    """``Signals`` that ignore what they are told: these tests publish nothing."""

    def signal(self, channel: Channel) -> None:
        del channel


@dataclass(slots=True, kw_only=True)
class Runs:
    """What the work of a request does each time it runs, and how many times it ran."""

    count: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    holding: asyncio.Event | None = None
    """When set, each run waits for it before it answers: a request still being handled."""
    answer: StoredResponse | None = None
    """What each run answers; by default a 201 naming the run."""
    fails: bool = False


@pytest.fixture
async def database(chassis: WorkerDatabase) -> AsyncGenerator[Database]:
    """Two connections: a twin runs beside the unit that holds its key."""
    opened = Database.create(app_url(chassis), pool_size=2)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
def probe(database: Database) -> BoundDatabase:
    return BoundDatabase(
        database,
        schema="probe",
        source="probe",
        jobs={MARK: JobPolicy(max_attempts=1, deadline=None)},
        signals=Silent(),
    )


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


async def count(admin: psycopg.AsyncConnection, query: LiteralString) -> int:
    row = await (await admin.execute(query)).fetchone()
    return _COUNT.validate_python(None if row is None else row[0])


def attempt(
    *,
    subject: str = "2001",
    route: str = CREATE,
    path: str = "/v1/things",
    key: str = KEY,
    name: str = "first",
) -> Attempt:
    caller = Principal(
        subject=SubjectId(subject), organization=ORGANIZATION, provider_organization=PROVIDER_ORGANIZATION
    )
    body = canonical_body(NewThing(name=name))
    return Attempt.of(caller, key, route=route, target=Target.of(path), body=body)


def moded(mode: Mode = Mode.TEST) -> OrganizationScope:
    return OrganizationScope(ORGANIZATION, mode)


async def run_once(
    probe: BoundDatabase,
    tried: Attempt,
    runs: Runs,
    *,
    scope: Scope | None = None,
    setting: LiteralString | None = None,
) -> tuple[StoredResponse, bool]:
    """``tried`` in a unit of its own: ``scope`` (by default test mode), then ``SET LOCAL setting``."""
    async with probe.unit(moded() if scope is None else scope) as unit:
        if setting is not None:
            await unit.connection.execute(text(f"SET LOCAL {setting}"))

        async def work() -> StoredResponse:
            runs.count += 1
            runs.started.set()
            if runs.holding is not None:
                await runs.holding.wait()
            unit.jobs.enqueue(MARK, f"run-{uuid.uuid7()}")
            if runs.fails:
                raise LookupError
            made = Thing(id=runs.count, name="made")
            return runs.answer or json_response(201, made, {"Location": f"/v1/things/{runs.count}"})

        outcome = await Idempotency().once(unit, tried, work)
    return outcome.response, outcome.replayed


async def test_the_first_use_runs_and_a_retry_replays_the_same_bytes(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    runs = Runs()
    first, first_replayed = await run_once(probe, attempt(), runs)
    again, again_replayed = await run_once(probe, attempt(), runs)
    assert (runs.count, first_replayed, again_replayed) == (1, False, True)
    assert (again.status, again.body, dict(again.headers)) == (first.status, first.body, dict(first.headers))
    assert (first.status, first.body) == (201, b'{"id":1,"name":"made"}')
    assert dict(first.headers) == {"Content-Type": "application/json", "Location": "/v1/things/1"}
    assert await count(admin, MARKS) == 1


async def test_another_body_under_the_same_key_is_refused(probe: BoundDatabase) -> None:
    runs = Runs()
    await run_once(probe, attempt(), runs)
    with pytest.raises(IdempotencyKeyReusedError) as caught:
        await run_once(probe, attempt(name="second"), runs)
    assert caught.value.code == "idempotency_key_reused"
    assert runs.count == 1


async def test_the_same_key_on_another_resource_of_the_route_is_refused(probe: BoundDatabase) -> None:
    runs = Runs()
    on_first = attempt(route="POST /v1/things/{thing_id}/notes", path="/v1/things/1/notes")
    on_second = attempt(route="POST /v1/things/{thing_id}/notes", path="/v1/things/2/notes")
    await run_once(probe, on_first, runs)
    with pytest.raises(IdempotencyKeyReusedError):
        await run_once(probe, on_second, runs)
    assert runs.count == 1


async def test_keys_are_scoped_per_route_mode_and_principal(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    runs = Runs()
    uses = [
        (attempt(route="POST /v1/a"), moded(Mode.TEST)),
        (attempt(route="POST /v1/b"), moded(Mode.TEST)),
        (attempt(route="POST /v1/a"), moded(Mode.LIVE)),
        (attempt(route="POST /v1/a", subject="2002"), moded(Mode.TEST)),
    ]
    replays = [(await run_once(probe, tried, runs, scope=scope))[1] for tried, scope in uses]
    assert (replays, runs.count) == ([False, False, False, False], 4)
    assert await count(admin, KEYS) == 4
    assert await count(admin, MARKS) == 4


async def test_a_failed_request_keeps_nothing(probe: BoundDatabase, admin: psycopg.AsyncConnection) -> None:
    runs = Runs(fails=True)
    with pytest.raises(LookupError):
        await run_once(probe, attempt(), runs)
    assert (await count(admin, KEYS), await count(admin, MARKS)) == (0, 0)
    runs.fails = False
    _, replayed = await run_once(probe, attempt(), runs)
    assert (replayed, runs.count) == (False, 2)


async def test_a_twin_waits_for_the_first_then_replays(chassis: WorkerDatabase, probe: BoundDatabase) -> None:
    first_runs = Runs(holding=asyncio.Event())
    twin_runs = Runs()
    first = asyncio.create_task(run_once(probe, attempt(), first_runs))
    await first_runs.started.wait()
    twin = asyncio.create_task(run_once(probe, attempt(), twin_runs))
    await eventually(lambda: waits_for_a_lock(app_url(chassis)))
    assert first_runs.holding is not None
    first_runs.holding.set()
    (answered, replayed_first), (replayed, is_replay) = await first, await twin
    assert (replayed_first, is_replay, twin_runs.count) == (False, True, 0)
    assert (replayed.status, replayed.body) == (answered.status, answered.body)


async def test_a_twin_that_waits_too_long_is_told_the_key_is_in_use(probe: BoundDatabase) -> None:
    first_runs = Runs(holding=asyncio.Event())
    first = asyncio.create_task(run_once(probe, attempt(), first_runs))
    await first_runs.started.wait()
    try:
        with pytest.raises(IdempotencyKeyInUseError) as caught:
            await run_once(probe, attempt(), Runs(), setting="lock_timeout = '100ms'")
        assert (caught.value.code, caught.value.kind.value) == ("idempotency_key_in_use", "conflict")
    finally:
        assert first_runs.holding is not None
        first_runs.holding.set()
        await first


async def test_an_expired_key_runs_again(probe: BoundDatabase, admin: psycopg.AsyncConnection) -> None:
    runs = Runs()
    await run_once(probe, attempt(), runs)
    await admin.execute("UPDATE probe.idempotency_keys SET expires_at = now() - interval '1 second'")
    stored, replayed = await run_once(probe, attempt(name="after expiry"), runs)
    assert (replayed, runs.count, stored.body) == (False, 2, b'{"id":2,"name":"made"}')
    replay, replayed = await run_once(probe, attempt(name="after expiry"), runs)
    assert (replayed, replay.body) == (True, stored.body)
    assert await count(admin, KEYS) == 1


@pytest.mark.parametrize(
    "scope", [OrganizationScope(ORGANIZATION, None), None], ids=["mode-less", "unscoped"]
)
async def test_a_unit_without_a_mode_cannot_hold_keys(
    probe: BoundDatabase, scope: OrganizationScope | None
) -> None:
    """Refused before any statement: a failed one would make the unit's exit raise."""
    runs = Runs()

    async def work() -> StoredResponse:
        runs.count += 1
        return json_response(201, Thing(id=1, name="made"))

    async with probe.unit(scope) as unit:
        with pytest.raises(UnmodedIdempotencyScopeError) as caught:
            await Idempotency().once(unit, attempt(), work)
    assert (caught.value.code, runs.count) == ("idempotency_scope_unmoded", 0)


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (StoredResponse(500, b"{}", {}), ServerFailureResponseError),
        (StoredResponse(201, b"x" * (MAX_STORED_BODY + 1), {}), OversizedStoredResponseError),
    ],
    ids=["server-failure", "huge"],
)
async def test_server_failures_and_huge_answers_are_not_stored(
    probe: BoundDatabase, admin: psycopg.AsyncConnection, answer: StoredResponse, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await run_once(probe, attempt(), Runs(answer=answer))
    assert (await count(admin, KEYS), await count(admin, MARKS)) == (0, 0)


async def test_a_twin_cancelled_by_another_timeout_fails_as_it_is(probe: BoundDatabase) -> None:
    """Only a lock wait means the key is in use: any other failure of the claim is the database's own."""
    first_runs = Runs(holding=asyncio.Event())
    first = asyncio.create_task(run_once(probe, attempt(), first_runs))
    await first_runs.started.wait()
    try:
        with pytest.raises(OperationalError) as caught:
            await run_once(probe, attempt(), Runs(), setting="statement_timeout = '100ms'")
        assert isinstance(caught.value.orig, psycopg.errors.QueryCanceled)
    finally:
        assert first_runs.holding is not None
        first_runs.holding.set()
        await first


async def test_a_committed_key_without_an_answer_is_a_defect(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    """The claim and the answer commit together; a row that has one without the other is never replayed."""
    tried = attempt()
    await admin.execute(
        """
        INSERT INTO probe.idempotency_keys
            (organization_id, mode, principal, route, key, request_hash, created_at, expires_at)
        VALUES (%s, 'test', %s, %s, %s, %s, now(), now() + interval '1 day')
        """,
        (ORGANIZATION, tried.principal, tried.route, tried.key, tried.request_hash),
    )
    with pytest.raises(UnansweredIdempotencyKeyError):
        await run_once(probe, tried, Runs())


def test_a_retention_of_nothing_is_refused() -> None:
    with pytest.raises(InvalidOptionError, match="retention"):
        Idempotency(retention=timedelta(0))
