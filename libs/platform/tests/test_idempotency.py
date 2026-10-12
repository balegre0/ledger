import asyncio
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Column, Integer, MetaData, Table, Text, Uuid, func, insert, select, text, update
from sqlalchemy.exc import OperationalError

from ledger_kernel.access import Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import OrganizationId, new_id
from ledger_platform.database import Database, DatabaseLimits
from ledger_platform.idempotency import (
    MAX_KEY_LENGTH,
    Attempt,
    Idempotency,
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    IdempotencyRecord,
    InvalidIdempotencyKeyError,
    InvalidIdempotencyOptionError,
    Outcome,
    StoredResponse,
    UnansweredIdempotencyKeyError,
)
from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES
from ledger_testing.waiting import PATIENCE, eventually, waits_for_a_lock
lazy from ledger_platform.database import UnitOfWork

ORGANIZATION: Final = OrganizationId(new_id())
STRANGERS: Final = OrganizationId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
BOB: Final = Principal(subject=SubjectId("bob"), organization=ORGANIZATION, provider_organization="1001")
MALLORY: Final = Principal(subject=SubjectId("alice"), organization=STRANGERS, provider_organization="1002")
KEYS_TABLE: Final = CHASSIS_METADATA.tables["idempotency_keys"]
CREATED: Final = StoredResponse(201, b'{"id": "thing-1"}', {"Location": "/v1/things/thing-1", "ETag": '"1"'})
ANOTHER_BODY: Final = b'{"name": "another thing"}'
IDEMPOTENCY: Final = Idempotency()
A_SECOND: Final = timedelta(seconds=1)

metadata = MetaData()
effects = Table(
    "effects", metadata, Column("id", Uuid(), primary_key=True), Column("label", Text, nullable=False)
)

type Work = Callable[[UnitOfWork], Awaitable[StoredResponse]]


def attempt(principal: Principal = ALICE, key: str = "key-1", body: bytes = b'{"name": "thing"}') -> Attempt:
    return Attempt.of(principal, key, method="POST", path="/v1/things", body=body)


async def create(unit: UnitOfWork) -> StoredResponse:
    """The work of a POST: one state change, and the response that announces it."""
    await unit.connection.execute(insert(effects).values(id=new_id(), label="created"))
    return CREATED


async def fail(unit: UnitOfWork) -> StoredResponse:
    await unit.connection.execute(insert(effects).values(id=new_id(), label="lost"))
    raise ZeroDivisionError


@final
class Held:
    """A work that holds its transaction (and its claim) open until released, then does ``then``.

    ``running`` is set once the work runs: the claim's row is written and its transaction still open.
    """

    def __init__(self, then: Work = create) -> None:
        self.running = asyncio.Event()
        self.released = asyncio.Event()
        self._then = then

    async def __call__(self, unit: UnitOfWork) -> StoredResponse:
        self.running.set()
        await self.released.wait()
        return await self._then(unit)


async def respond(
    database: Database, tried: Attempt, work: Work = create, idempotency: Idempotency = IDEMPOTENCY
) -> Outcome:
    async with database.transaction(tried.organization) as unit:
        return await idempotency.once(unit, tried, lambda: work(unit))


async def holding(database: Database, held: Held, tried: Attempt | None = None) -> asyncio.Task[Outcome]:
    """A request with ``tried`` (``attempt()`` by default) in a task of its own, once it holds its claim."""
    task = asyncio.create_task(respond(database, tried or attempt(), held))
    await held.running.wait()
    return task


async def count_effects(database: Database) -> int:
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(select(func.count()).select_from(effects))).scalar_one()


async def stored_keys(database: Database, organization: OrganizationId) -> list[str]:
    async with database.transaction(organization) as unit:
        rows = await unit.connection.execute(select(IdempotencyRecord.key).order_by(IdempotencyRecord.key))
        return list(rows.scalars())


async def expire(
    database: Database, organization: OrganizationId, *only: str, ago: timedelta = A_SECOND
) -> None:
    """Make the organization's keys (``only`` those, if given) expired for ``ago`` already."""
    expired = update(IdempotencyRecord).values(expires_at=func.now() - ago)
    if only:
        expired = expired.where(IdempotencyRecord.key.in_(only))
    async with database.transaction(organization) as unit:
        await unit.connection.execute(expired)


@pytest.fixture
async def keys(database: Database) -> AsyncGenerator[Database]:
    """The chassis's tables under their policies (as a migration leaves them), and a table of effects."""
    async with database.engine.begin() as connection:
        for statement in CHASSIS_POLICIES:
            await connection.execute(text(statement))
        await connection.run_sync(metadata.create_all)
    yield database
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)


# ── Once ────────────────────────────────────────────────────────────────────────


async def test_the_first_request_runs_and_a_retry_replays_its_response(keys: Database) -> None:
    first = await respond(keys, attempt())
    again = await respond(keys, attempt())
    assert first == Outcome(CREATED, replayed=False)
    assert again == Outcome(CREATED, replayed=True)
    assert (again.response.status, again.response.body) == (201, CREATED.body)  # the very bytes
    assert dict(again.response.headers) == dict(CREATED.headers)
    assert await count_effects(keys) == 1


async def test_the_same_key_with_another_request_is_refused(keys: Database) -> None:
    await respond(keys, attempt())
    with pytest.raises(IdempotencyKeyReusedError) as caught:
        await respond(keys, attempt(body=ANOTHER_BODY))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "idempotency_key_reused")
    assert await count_effects(keys) == 1


async def test_a_key_belongs_to_its_caller(keys: Database) -> None:
    await respond(keys, attempt(ALICE))
    assert not (await respond(keys, attempt(BOB))).replayed
    assert not (await respond(keys, attempt(MALLORY))).replayed
    assert await count_effects(keys) == 3


async def test_a_request_that_fails_leaves_the_key_free(keys: Database) -> None:
    with pytest.raises(ZeroDivisionError):
        await respond(keys, attempt(), fail)
    assert await stored_keys(keys, ORGANIZATION) == []
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=False)
    assert await count_effects(keys) == 1


# ── Two requests with one key at once ───────────────────────────────────────────


async def test_two_requests_with_one_key_at_once_run_the_work_once(keys: Database, database_url: str) -> None:
    held = Held()
    first = await holding(keys, held)
    second = asyncio.create_task(respond(keys, attempt()))
    await eventually(lambda: waits_for_a_lock(database_url))  # the twin's claim waits for the first's row
    assert not second.done()
    held.released.set()
    assert (await first, await second) == (Outcome(CREATED, replayed=False), Outcome(CREATED, replayed=True))
    assert await count_effects(keys) == 1


async def test_a_twin_with_another_request_waits_and_is_refused(keys: Database, database_url: str) -> None:
    held = Held()
    first = await holding(keys, held)
    second = asyncio.create_task(respond(keys, attempt(body=ANOTHER_BODY)))
    await eventually(lambda: waits_for_a_lock(database_url))
    held.released.set()
    assert await first == Outcome(CREATED, replayed=False)
    with pytest.raises(IdempotencyKeyReusedError):
        await second
    assert await count_effects(keys) == 1


async def test_a_twin_of_a_request_that_fails_does_the_work_itself(keys: Database, database_url: str) -> None:
    held = Held(then=fail)
    first = await holding(keys, held)
    second = asyncio.create_task(respond(keys, attempt()))
    await eventually(lambda: waits_for_a_lock(database_url))
    held.released.set()
    with pytest.raises(ZeroDivisionError):
        await first
    assert await second == Outcome(CREATED, replayed=False)
    assert await count_effects(keys) == 1


async def test_a_twin_that_waits_longer_than_the_lock_timeout_is_told_to_retry(
    keys: Database, database_url: str
) -> None:
    impatient_limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=200))
    async with Database.open(database_url, pool_size=1, limits=impatient_limits) as impatient:
        held = Held()
        first = await holding(keys, held)
        with pytest.raises(IdempotencyKeyInUseError) as caught:
            await respond(impatient, attempt())
        held.released.set()
        await first
    assert (caught.value.kind, caught.value.code) == (ErrorKind.CONFLICT, "idempotency_key_in_use")
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=True)
    assert await count_effects(keys) == 1


async def test_a_claim_cancelled_for_another_reason_is_not_taken_for_a_key_in_use(
    keys: Database, database_url: str
) -> None:
    hasty_limits = DatabaseLimits(statement_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=hasty_limits) as hasty:
        held = Held()
        first = await holding(keys, held)
        with pytest.raises(OperationalError) as caught:
            await respond(hasty, attempt())
        held.released.set()
        await first
    assert isinstance(
        caught.value.orig, psycopg.errors.QueryCanceled
    )  # the statement timeout, not the lock's


# ── Retention ───────────────────────────────────────────────────────────────────


async def test_a_key_older_than_its_retention_runs_the_work_again(keys: Database) -> None:
    await respond(keys, attempt())
    await expire(keys, ORGANIZATION)
    assert await respond(keys, attempt(body=ANOTHER_BODY)) == Outcome(CREATED, replayed=False)
    assert await respond(keys, attempt(body=ANOTHER_BODY)) == Outcome(CREATED, replayed=True)
    assert await count_effects(keys) == 2


async def test_an_expired_key_the_purge_left_is_taken_over_by_its_next_request(keys: Database) -> None:
    """The purge deletes the oldest keys first: a batch of one takes ``older`` and leaves ``key-1``."""
    await respond(keys, attempt(key="older"))
    await respond(keys, attempt())
    await expire(keys, ORGANIZATION, "older", ago=timedelta(hours=1))
    await expire(keys, ORGANIZATION, "key-1")
    taken_over = await respond(keys, attempt(body=ANOTHER_BODY), idempotency=Idempotency(purge_batch=1))
    assert taken_over == Outcome(CREATED, replayed=False)
    assert await stored_keys(keys, ORGANIZATION) == ["key-1"]
    assert await respond(keys, attempt(body=ANOTHER_BODY)) == Outcome(CREATED, replayed=True)
    with pytest.raises(IdempotencyKeyReusedError):  # the key is the later request's now
        await respond(keys, attempt())
    assert await count_effects(keys) == 3


async def test_a_key_is_kept_for_the_retention_from_its_first_use(keys: Database) -> None:
    await respond(keys, attempt(key="an-hour"), idempotency=Idempotency(retention=timedelta(hours=1)))
    await respond(keys, attempt(key="a-day"))
    async with keys.transaction(ORGANIZATION) as unit:
        rows = await unit.connection.execute(
            select(IdempotencyRecord.key, IdempotencyRecord.expires_at, func.now())
        )
        left = {key: expires_at - now for key, expires_at, now in rows}
    assert timedelta(minutes=59) < left["an-hour"] <= timedelta(hours=1)
    assert timedelta(hours=23, minutes=59) < left["a-day"] <= timedelta(hours=24)


async def test_expired_keys_of_the_organization_are_purged_by_its_later_requests(keys: Database) -> None:
    for key in ("old-1", "old-2", "old-3"):
        await respond(keys, attempt(key=key))
    await respond(keys, attempt(MALLORY, key="theirs"))
    await expire(keys, ORGANIZATION)
    await expire(keys, STRANGERS)
    await respond(keys, attempt(key="new"), idempotency=Idempotency(purge_batch=2))
    assert len(await stored_keys(keys, ORGANIZATION)) == 2  # one expired key left, and the new one
    assert await stored_keys(keys, STRANGERS) == ["theirs"]  # another organization's are not touched


async def test_the_purge_skips_an_expired_key_another_request_holds(keys: Database) -> None:
    await respond(keys, attempt(key="busy"))
    await expire(keys, ORGANIZATION)
    held = Held()
    taking_over = await holding(keys, held, attempt(key="busy", body=ANOTHER_BODY))
    async with asyncio.timeout(PATIENCE):  # a purge that waited for ``busy`` would wait until the release
        assert await respond(keys, attempt(key="new")) == Outcome(CREATED, replayed=False)
    held.released.set()
    assert await taking_over == Outcome(CREATED, replayed=False)
    assert await stored_keys(keys, ORGANIZATION) == ["busy", "new"]


async def test_an_organization_sees_only_its_own_keys(keys: Database) -> None:
    await respond(keys, attempt())
    assert await stored_keys(keys, STRANGERS) == []
    assert await stored_keys(keys, ORGANIZATION) == ["key-1"]


async def test_a_committed_key_without_a_response_is_a_defect(keys: Database) -> None:
    tried = attempt()
    async with keys.transaction(ORGANIZATION) as unit:
        await unit.connection.execute(
            insert(IdempotencyRecord).values(
                organization_id=tried.organization,
                subject=tried.subject,
                key=tried.key,
                fingerprint=tried.fingerprint,
                expires_at=func.now() + timedelta(hours=1),
            )
        )
    with pytest.raises(UnansweredIdempotencyKeyError) as caught:
        await respond(keys, tried)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "unanswered_idempotency_key")


# ── Attempts and options ────────────────────────────────────────────────────────


def test_the_fingerprint_is_of_the_method_the_path_and_the_body() -> None:
    base = Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"{}")
    assert base == Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"{}")
    assert base.fingerprint != Attempt.of(ALICE, "k", method="PUT", path="/v1/things", body=b"{}").fingerprint
    assert base.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/other", body=b"{}").fingerprint
    assert (
        base.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=b"[]").fingerprint
    )


def test_the_parts_of_a_fingerprint_cannot_be_shifted_into_each_other() -> None:
    shifted = Attempt.of(ALICE, "k", method="POST", path="/v1/ab", body=b"c")
    assert shifted.fingerprint != Attempt.of(ALICE, "k", method="POST", path="/v1/a", body=b"bc").fingerprint


requests = st.tuples(st.sampled_from(["POST", "PUT", "PATCH", "DELETE"]), st.text(), st.binary())


@given(one=requests, other=requests)
def test_two_requests_share_a_fingerprint_exactly_when_they_are_the_same(
    one: tuple[str, str, bytes], other: tuple[str, str, bytes]
) -> None:
    def fingerprint(method: str, path: str, body: bytes) -> bytes:
        return Attempt.of(ALICE, "k", method=method, path=path, body=body).fingerprint

    assert (fingerprint(*one) == fingerprint(*other)) is (one == other)


def test_a_key_is_its_callers() -> None:
    tried = attempt(BOB)
    assert (tried.organization, tried.subject, tried.key) == (ORGANIZATION, "bob", "key-1")


@pytest.mark.parametrize(
    "key",
    ["", "with space", "tab\t", "café", "x" * (MAX_KEY_LENGTH + 1)],
    ids=["empty", "space", "tab", "non-ascii", "too-long"],
)
def test_a_key_is_visible_ascii_of_bounded_length(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError) as caught:
        attempt(key=key)
    assert len(str(caught.value)) < 160
    assert caught.value.code == "invalid_idempotency_key"


def test_the_longest_key_is_accepted() -> None:
    assert attempt(key="k" * MAX_KEY_LENGTH).key == "k" * MAX_KEY_LENGTH


@pytest.mark.parametrize(
    ("build", "option"),
    [
        (lambda: Idempotency(retention=timedelta(0)), "retention"),
        (lambda: Idempotency(retention=timedelta(seconds=-1)), "retention"),
        (lambda: Idempotency(purge_batch=0), "purge_batch"),
    ],
)
def test_options_that_cannot_work_are_refused(build: Callable[[], Idempotency], option: str) -> None:
    with pytest.raises(InvalidIdempotencyOptionError, match=f"idempotency option {option} must be"):
        build()


def test_the_table_has_the_columns_the_migrations_create() -> None:
    assert [column.name for column in KEYS_TABLE.columns] == [
        "organization_id",
        "subject",
        "key",
        "fingerprint",
        "status",
        "headers",
        "body",
        "expires_at",
    ]
    assert {index.name for index in KEYS_TABLE.indexes} == {"ix_idempotency_keys_expiry"}
    assert isinstance(KEYS_TABLE.c.status.type, Integer)
