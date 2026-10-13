import asyncio
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, MetaData, Table, Text, Uuid, func, insert, select, text, update
from sqlalchemy.exc import OperationalError

from ledger_kernel.access import Principal, SubjectId
from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.data.database import Database, DatabaseLimits
from ledger_platform.idempotency import (
    MAX_KEY_LENGTH,
    MAX_STORED_BODY,
    Attempt,
    Idempotency,
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    IdempotencyRecord,
    InvalidIdempotencyKeyError,
    InvalidIdempotencyOptionError,
    MismatchedIdempotencyScopeError,
    Outcome,
    OversizedStoredResponseError,
    ServerFailureResponseError,
    StoredResponse,
    UnansweredIdempotencyKeyError,
    canonical_body,
)
from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES
from ledger_testing.waiting import PATIENCE, eventually, waits_for_a_lock
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import UnitOfWork

ORGANIZATION: Final = OrganizationId(new_id())
STRANGERS: Final = OrganizationId(new_id())
ALICE: Final = Principal(subject=SubjectId("alice"), organization=ORGANIZATION, provider_organization="1001")
BOB: Final = Principal(subject=SubjectId("bob"), organization=ORGANIZATION, provider_organization="1001")
MALLORY: Final = Principal(subject=SubjectId("alice"), organization=STRANGERS, provider_organization="1002")
KEYS_TABLE: Final = CHASSIS_METADATA.tables["idempotency_keys"]
CREATED: Final = StoredResponse(
    201, b'{"id": "thing-1"}', {"Location": "/v1/things/thing-1", "ETag": '"1"', "Cache-Control": "no-store"}
)
"""Its headers are in no sorted order (a JSONB object would reorder them by length)."""
ANOTHER: Final = "another thing"
IDEMPOTENCY: Final = Idempotency()
A_SECOND: Final = timedelta(seconds=1)

metadata = MetaData()
effects = Table(
    "effects", metadata, Column("id", Uuid(), primary_key=True), Column("label", Text, nullable=False)
)

type Work = Callable[[UnitOfWork], Awaitable[StoredResponse]]


class Thing(BaseModel):
    """The body of a request, validated."""

    name: str
    tags: dict[str, int] = Field(default_factory=dict[str, int])


def attempt(principal: Principal = ALICE, key: str = "key-1", name: str = "thing") -> Attempt:
    return Attempt.of(principal, key, method="POST", path="/v1/things", body=canonical_body(Thing(name=name)))


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


def answering(response: StoredResponse) -> Work:
    """A work that changes state, as ``create`` does, and answers ``response``."""

    async def work(unit: UnitOfWork) -> StoredResponse:
        await create(unit)
        return response

    return work


async def respond(
    database: Database,
    tried: Attempt,
    work: Work = create,
    idempotency: Idempotency = IDEMPOTENCY,
    scope: Scope | None = None,
) -> Outcome:
    """``tried``'s request in a transaction of its own, scoped to ``scope`` or else its organization."""
    async with database.transaction(scope or OrganizationScope(tried.organization, None)) as unit:
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
    async with database.transaction(OrganizationScope(organization, None)) as unit:
        rows = await unit.connection.execute(select(IdempotencyRecord.key).order_by(IdempotencyRecord.key))
        return list(rows.scalars())


async def expire(
    database: Database, organization: OrganizationId, *only: str, ago: timedelta = A_SECOND
) -> None:
    """Make the organization's keys (``only`` those, if given) expired for ``ago`` already."""
    expired = update(IdempotencyRecord).values(expires_at=func.now() - ago)
    if only:
        expired = expired.where(IdempotencyRecord.key.in_(only))
    async with database.transaction(OrganizationScope(organization, None)) as unit:
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
    assert list(again.response.headers.items()) == list(CREATED.headers.items())  # in the same order
    assert await count_effects(keys) == 1


async def test_the_same_key_with_another_request_is_refused(keys: Database) -> None:
    await respond(keys, attempt())
    with pytest.raises(IdempotencyKeyReusedError) as caught:
        await respond(keys, attempt(name=ANOTHER))
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


async def test_a_client_error_is_stored_and_replayed(keys: Database) -> None:
    refused = StoredResponse(422, b'{"code": "invalid_input"}', {"Content-Type": "application/problem+json"})
    assert await respond(keys, attempt(), answering(refused)) == Outcome(refused, replayed=False)
    assert await respond(keys, attempt()) == Outcome(refused, replayed=True)


async def test_the_headers_a_replay_must_not_repeat_are_not_stored(keys: Database) -> None:
    unrepeatable = {
        "set-cookie": "session=1",
        "Authorization": "Bearer token",
        "Date": "Mon, 12 Oct 2026 10:00:00 GMT",
        "Content-Length": "17",
        "Connection": "close",
        "Keep-Alive": "timeout=5",
        "Proxy-Authenticate": "Basic",
        "Proxy-Authorization": "Basic e30=",
        "TE": "trailers",
        "Trailer": "Expires",
        "Transfer-Encoding": "chunked",
        "Upgrade": "h2c",
    }
    answered = StoredResponse(
        201, CREATED.body, {"Location": "/v1/things/thing-1", **unrepeatable, "ETag": '"1"'}
    )
    first = await respond(keys, attempt(), answering(answered))
    again = await respond(keys, attempt())
    assert first == Outcome(answered, replayed=False)  # the first answer is the work's own
    assert list(again.response.headers.items()) == [("Location", "/v1/things/thing-1"), ("ETag", '"1"')]


@pytest.mark.parametrize("status", [500, 503, 599])
async def test_a_server_failure_is_not_stored_and_leaves_the_key_free(keys: Database, status: int) -> None:
    failure = StoredResponse(status, b'{"code": "unavailable"}', {})
    with pytest.raises(ServerFailureResponseError) as caught:
        await respond(keys, attempt(), answering(failure))
    assert (caught.value.response, caught.value.kind) == (failure, ErrorKind.INTERNAL)
    assert (await stored_keys(keys, ORGANIZATION), await count_effects(keys)) == ([], 0)
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=False)


async def test_a_body_up_to_the_bound_is_stored(keys: Database) -> None:
    largest = StoredResponse(200, b"x" * MAX_STORED_BODY, {})
    await respond(keys, attempt(), answering(largest))
    assert await respond(keys, attempt()) == Outcome(largest, replayed=True)


async def test_a_body_above_the_bound_is_a_defect_and_leaves_the_key_free(keys: Database) -> None:
    with pytest.raises(OversizedStoredResponseError) as caught:
        await respond(keys, attempt(), answering(StoredResponse(200, b"x" * (MAX_STORED_BODY + 1), {})))
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "oversized_stored_response")
    assert (await stored_keys(keys, ORGANIZATION), await count_effects(keys)) == ([], 0)


async def test_a_unit_scoped_to_a_company_of_the_organization_is_accepted(keys: Database) -> None:
    company = Tenant(ORGANIZATION, CompanyId(new_id()), None)
    assert await respond(keys, attempt(), scope=company) == Outcome(CREATED, replayed=False)
    assert await respond(keys, attempt()) == Outcome(CREATED, replayed=True)


@pytest.mark.parametrize(
    "scope",
    [OrganizationScope(STRANGERS, None), Tenant(STRANGERS, CompanyId(new_id()), None)],
    ids=["another organization", "a company of another organization"],
)
async def test_a_unit_scoped_to_another_organization_is_a_defect(keys: Database, scope: Scope) -> None:
    with pytest.raises(MismatchedIdempotencyScopeError) as caught:
        await respond(keys, attempt(), scope=scope)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "mismatched_idempotency_scope")
    assert await count_effects(keys) == 0


async def test_a_unit_without_a_scope_is_a_defect(keys: Database) -> None:
    with pytest.raises(MismatchedIdempotencyScopeError):
        async with keys.transaction(None) as unit:
            await IDEMPOTENCY.once(unit, attempt(), lambda: create(unit))
    assert await count_effects(keys) == 0


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
    second = asyncio.create_task(respond(keys, attempt(name=ANOTHER)))
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
    assert await respond(keys, attempt(name=ANOTHER)) == Outcome(CREATED, replayed=False)
    assert await respond(keys, attempt(name=ANOTHER)) == Outcome(CREATED, replayed=True)
    assert await count_effects(keys) == 2


async def test_an_expired_key_the_purge_left_is_taken_over_by_its_next_request(keys: Database) -> None:
    """The claim comes before the purge, which takes the oldest key first: a batch of one takes ``older``."""
    await respond(keys, attempt(key="older"))
    await respond(keys, attempt())
    await expire(keys, ORGANIZATION, "older", ago=timedelta(hours=1))
    await expire(keys, ORGANIZATION, "key-1")
    taken_over = await respond(keys, attempt(name=ANOTHER), idempotency=Idempotency(purge_batch=1))
    assert taken_over == Outcome(CREATED, replayed=False)
    assert await stored_keys(keys, ORGANIZATION) == ["key-1"]
    assert await respond(keys, attempt(name=ANOTHER)) == Outcome(CREATED, replayed=True)
    with pytest.raises(IdempotencyKeyReusedError):  # the key is the later request's now
        await respond(keys, attempt())
    assert await count_effects(keys) == 3


async def test_a_key_is_kept_for_the_retention_from_its_first_use(keys: Database) -> None:
    await respond(keys, attempt(key="an-hour"), idempotency=Idempotency(retention=timedelta(hours=1)))
    await respond(keys, attempt(key="a-day"))
    async with keys.transaction(OrganizationScope(ORGANIZATION, None)) as unit:
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
    taking_over = await holding(keys, held, attempt(key="busy", name=ANOTHER))
    async with asyncio.timeout(PATIENCE):  # a purge that waited for ``busy`` would wait until the release
        assert await respond(keys, attempt(key="new")) == Outcome(CREATED, replayed=False)
    held.released.set()
    assert await taking_over == Outcome(CREATED, replayed=False)
    assert await stored_keys(keys, ORGANIZATION) == ["busy", "new"]


async def test_two_requests_whose_keys_both_expired_both_complete(keys: Database, database_url: str) -> None:
    """The one that purges the other's key holds it until it ends; the other waits for it, holding no lock.

    Each request claims its own key before it purges, and a purge skips the keys others hold, so a request
    that waits has locked nothing anyone waits for. With the purge first, each could delete the other's key,
    then wait for its own, which the other had deleted: a cycle, broken only by the lock timeout.
    """
    for key in ("mine", "yours"):
        await respond(keys, attempt(key=key))
    await expire(keys, ORGANIZATION)
    held = Held()
    mine = await holding(keys, held, attempt(key="mine", name=ANOTHER))  # claimed "mine", purged "yours"
    yours = asyncio.create_task(respond(keys, attempt(key="yours", name=ANOTHER)))
    await eventually(lambda: waits_for_a_lock(database_url))  # for the purge of "yours" to end
    held.released.set()
    assert (await mine, await yours) == (Outcome(CREATED, replayed=False), Outcome(CREATED, replayed=False))
    assert await stored_keys(keys, ORGANIZATION) == ["mine", "yours"]


async def test_an_organization_sees_only_its_own_keys(keys: Database) -> None:
    await respond(keys, attempt())
    assert await stored_keys(keys, STRANGERS) == []
    assert await stored_keys(keys, ORGANIZATION) == ["key-1"]


async def test_a_committed_key_without_a_response_is_a_defect(keys: Database) -> None:
    tried = attempt()
    async with keys.transaction(OrganizationScope(ORGANIZATION, None)) as unit:
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


def fingerprint(method: str, path: str, name: str | None) -> bytes:
    """The fingerprint of ``method path`` with a ``Thing`` named ``name`` as its body, or none."""
    body = canonical_body(None if name is None else Thing(name=name))
    return Attempt.of(ALICE, "k", method=method, path=path, body=body).fingerprint


def test_the_fingerprint_is_of_the_method_the_path_and_the_body() -> None:
    base = fingerprint("POST", "/v1/things", "a")
    assert base == fingerprint("POST", "/v1/things", "a")
    assert base != fingerprint("PUT", "/v1/things", "a")
    assert base != fingerprint("POST", "/v1/other", "a")
    assert base != fingerprint("POST", "/v1/things", "b")
    assert base != fingerprint("POST", "/v1/things", None)


def test_the_parts_of_a_fingerprint_cannot_be_shifted_into_each_other() -> None:
    body = canonical_body(Thing(name="c")).decode()
    assert fingerprint("POST", f"/v1/a{body}", None) != fingerprint("POST", "/v1/a", "c")


requests = st.tuples(st.sampled_from(["POST", "PUT", "PATCH", "DELETE"]), st.text(), st.none() | st.text())


@given(one=requests, other=requests)
def test_two_requests_share_a_fingerprint_exactly_when_they_are_the_same(
    one: tuple[str, str, str | None], other: tuple[str, str, str | None]
) -> None:
    assert (fingerprint(*one) == fingerprint(*other)) is (one == other)


def test_a_canonical_body_ignores_the_order_of_keys_and_the_whitespace_of_the_json_sent() -> None:
    sent = Thing.model_validate_json('{"tags": {"b": 2, "a": 1}, "name": "x"}')
    resent = Thing.model_validate_json('{\n  "name" : "x",\n  "tags" : { "a" : 1, "b" : 2 }\n}')
    assert canonical_body(sent) == canonical_body(resent) == b'{"name":"x","tags":{"a":1,"b":2}}'
    assert Attempt.of(ALICE, "k", method="POST", path="/v1/things", body=canonical_body(sent)) == Attempt.of(
        ALICE, "k", method="POST", path="/v1/things", body=canonical_body(resent)
    )
    assert canonical_body(Thing.model_validate_json('{"name": "x"}')) == b'{"name":"x","tags":{}}'  # defaults
    assert canonical_body(Thing(name="\u00e9")) == '{"name":"\u00e9","tags":{}}'.encode()
    assert canonical_body(None) == b""


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
