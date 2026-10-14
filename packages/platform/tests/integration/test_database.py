import asyncio
from datetime import timedelta
from types import SimpleNamespace
lazy from collections.abc import AsyncGenerator

import psycopg
import pytest
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    column,
    func,
    literal_column,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import QueuePool
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import new_id
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.database import Database, DatabaseLimits, Pooling

metadata = MetaData()
notes = Table("notes", metadata, Column("id", Uuid(), primary_key=True), Column("body", Text, nullable=False))
UNREACHABLE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


async def backend_of(connection: AsyncConnection) -> int:
    return (await connection.execute(select(literal_column("pg_backend_pid()", Integer)))).scalar_one()


def sqlstate(error: DBAPIError) -> str | None:
    """The SQLSTATE PostgreSQL answered with: the code is contract, the wording of the message is not."""
    return error.orig.sqlstate if isinstance(error.orig, psycopg.Error) else None


async def libpq_parameters(database: Database) -> dict[str, str]:
    """What libpq was actually given for a connection of ``database`` (the password is never listed)."""
    async with database.engine.connect() as connection:
        driver = (await connection.get_raw_connection()).driver_connection
        assert isinstance(driver, psycopg.AsyncConnection)
        return driver.info.get_parameters()


async def sessions_named(database: Database, application_name: str) -> int:
    """How many sessions PostgreSQL holds for ``application_name`` on the test's database."""
    named = (
        select(func.count())
        .select_from(text("pg_stat_activity"))
        .where(
            column("application_name", Text) == application_name,
            column("datname", Text) == func.current_database(),
        )
    )
    async with database.engine.begin() as connection:
        return (await connection.execute(named)).scalar_one()


async def setting(database: Database, name: str) -> str:
    async with database.engine.begin() as connection:
        shown = literal_column(f"current_setting('{name}')", Text)
        return (await connection.execute(select(shown))).scalar_one()


# ── Ping and the pool ───────────────────────────────────────────────────────────


BOUND_VALUE = "a-value-that-must-not-leak"


async def fail_on_a_missing_table(database: Database, value: str) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(text("SELECT * FROM no_such_table WHERE name = :name"), {"name": value})


async def test_a_failing_statement_does_not_echo_the_values_bound_to_it(database: Database) -> None:
    """The text of an error ends up in logs: what was bound to the statement (a payload, a name) stays out."""
    with pytest.raises(ProgrammingError) as failed:
        await fail_on_a_missing_table(database, BOUND_VALUE)
    assert "no_such_table" in str(failed.value)
    assert BOUND_VALUE not in str(failed.value)


async def test_ping(database: Database) -> None:
    await database.ping()


async def test_ping_fails_when_postgresql_cannot_serve_the_database(database_url: str) -> None:
    absent = make_url(database_url).set(database="absent").render_as_string(hide_password=False)
    async with Database.open(absent, pool_size=1) as unserved:
        with pytest.raises(OperationalError, match='"absent" does not exist'):
            await unserved.ping()


async def touch(database: Database) -> None:
    """A transaction that does nothing but take a connection of the pool, and give it back."""
    async with database.engine.begin():
        pass


async def test_leaving_the_context_closes_the_pool(database_url: str) -> None:
    async with Database.open(database_url, pool_size=1) as opened:
        await touch(opened)
        pool = opened.engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.checkedin() == 1
    assert pool.checkedin() == 0


async def test_the_pool_is_closed_when_the_body_fails(database_url: str) -> None:
    pools: list[QueuePool] = []

    async def ping_then_fail() -> None:
        async with Database.open(database_url, pool_size=1) as opened:
            await touch(opened)
            pool = opened.engine.pool
            assert isinstance(pool, QueuePool)
            pools.append(pool)
            raise LookupError

    with pytest.raises(LookupError):
        await ping_then_fail()
    assert pools[0].checkedin() == 0


class DisposeFailedError(Exception):
    def __init__(self) -> None:
        super().__init__("the first engine would not dispose")


async def test_both_engines_are_disposed_even_when_the_first_cannot_be(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[AsyncEngine] = []
    real = AsyncEngine.dispose

    async def dispose(self: AsyncEngine, *, close: bool = True) -> None:
        disposed.append(self)
        await real(self, close=close)
        if len(disposed) == 1:
            raise DisposeFailedError

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    with pytest.raises(DisposeFailedError):
        async with Database.open(database_url, pool_size=1) as opened:
            await touch(opened)
    assert len(disposed) == 2
    assert disposed[0] is not disposed[1]


async def test_a_connection_that_postgresql_ended_is_replaced_before_it_is_used(
    database: Database, database_url: str
) -> None:
    async with Database.open(database_url, pool_size=1) as pooled:
        async with pooled.engine.begin() as connection:
            ended = await backend_of(connection)
        async with database.engine.begin() as admin:
            terminated = literal_column(
                f"pg_terminate_backend({ended}, 5000)", Boolean
            )  # waits until it is gone
            assert (await admin.execute(select(terminated))).scalar_one()
        async with pooled.engine.begin() as connection:
            replacement = await backend_of(connection)
    assert replacement != ended


async def test_a_full_pool_makes_the_caller_wait_only_for_the_pool_timeout(database_url: str) -> None:
    limits = DatabaseLimits(pool_timeout=timedelta(milliseconds=100))
    async with (
        Database.open(database_url, pool_size=1, limits=limits) as small,
        small.engine.connect(),
        small.engine.connect(),  # the pool and its one overflow
    ):
        with pytest.raises(PoolTimeoutError):
            await asyncio.wait_for(touch(small), timeout=5)  # the default pool timeout is ten seconds


async def test_a_ping_does_not_wait_for_a_full_pool(database_url: str) -> None:
    """Readiness asks whether PostgreSQL answers, not whether the pool has a connection to spare: a service
    whose requests hold every connection is busy, not broken, and must not leave the rotation for it."""
    async with (
        Database.open(database_url, pool_size=1) as busy,
        busy.engine.connect(),
        busy.engine.connect(),  # the pool and its one overflow
    ):
        await asyncio.wait_for(busy.ping(), timeout=2)  # the pool would make it wait ten seconds
        pool = busy.engine.pool
        assert isinstance(pool, QueuePool)
        assert (pool.checkedout(), pool.checkedin()) == (2, 0)  # the ping took none of the pool's


async def test_a_ping_leaves_no_connection_behind(database: Database, database_url: str) -> None:
    async with Database.open(database_url, pool_size=1, application_name="ledger-ping") as pinging:
        for _ in range(3):
            await pinging.ping()
        assert await sessions_named(database, "ledger-ping") == 0


async def test_a_connection_is_replaced_once_it_is_older_than_the_pool_recycle(database_url: str) -> None:
    limits = DatabaseLimits(pool_recycle=timedelta(seconds=1))
    async with Database.open(database_url, pool_size=1, limits=limits) as recycling:
        async with recycling.engine.begin() as connection:
            first = await backend_of(connection)
        async with recycling.engine.begin() as connection:
            soon_after = await backend_of(connection)
        await asyncio.sleep(1.2)
        async with recycling.engine.begin() as connection:
            much_later = await backend_of(connection)
    assert soon_after == first  # the same connection is reused...
    assert much_later != first  # ...until it is too old


async def test_the_default_connection_parameters_reach_libpq(database_url: str) -> None:
    async with Database.open(database_url, pool_size=1) as defaults:
        given = await libpq_parameters(defaults)
    assert given["connect_timeout"] == "5"
    assert given["application_name"] == "ledger"
    assert given["options"] == (
        "-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"
    )


async def test_the_given_connection_parameters_reach_libpq_and_a_fraction_of_a_second_rounds_up(
    database_url: str,
) -> None:
    limits = DatabaseLimits(
        connect_timeout=timedelta(milliseconds=2500),
        statement_timeout=timedelta(milliseconds=1500),
        lock_timeout=timedelta(seconds=2),
        idle_in_transaction_session_timeout=timedelta(minutes=2),
    )
    async with Database.open(
        database_url, pool_size=1, application_name="ledger-test", limits=limits
    ) as given:
        parameters = await libpq_parameters(given)
    assert parameters["connect_timeout"] == "3"  # never a shorter wait than asked for
    assert parameters["application_name"] == "ledger-test"
    assert parameters["options"] == (
        "-c statement_timeout=1500 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=120000"
    )


async def test_a_server_that_does_not_answer_is_given_up_on_after_the_connect_timeout() -> None:
    """A closed port. Where it is refused at once this proves little, hence the tests of what libpq is given.

    Where libpq alone would keep retrying it (Windows: over two minutes), it proves the timeout works.
    """
    limits = DatabaseLimits(connect_timeout=timedelta(seconds=1))
    async with Database.open(UNREACHABLE, pool_size=1, limits=limits) as unreachable:
        with pytest.raises(OperationalError):
            await asyncio.wait_for(unreachable.ping(), timeout=10)


# ── Limits on every session ─────────────────────────────────────────────────────


async def test_the_default_limits_are_in_force_on_every_session(database_url: str) -> None:
    async with Database.open(database_url, pool_size=1) as defaults:
        shown = [
            await setting(defaults, name)
            for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
        ]
        assert shown == ["30s", "5s", "1min"]
        assert await setting(defaults, "application_name") == "ledger"


async def test_the_limits_given_are_in_force_on_every_session(database_url: str) -> None:
    limits = DatabaseLimits(
        statement_timeout=timedelta(milliseconds=7500),
        lock_timeout=timedelta(seconds=3),
        idle_in_transaction_session_timeout=timedelta(seconds=90),
    )
    async with Database.open(
        database_url, pool_size=1, application_name="ledger-test", limits=limits
    ) as given:
        shown = [
            await setting(given, name)
            for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
        ]
        assert shown == ["7500ms", "3s", "90s"]
        assert await setting(given, "application_name") == "ledger-test"


async def sleep_in_postgres(database: Database, seconds: float) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": seconds})


async def test_a_statement_that_outlives_the_statement_timeout_is_cancelled(database_url: str) -> None:
    limits = DatabaseLimits(statement_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=limits) as impatient:
        with pytest.raises(DBAPIError) as caught:
            await sleep_in_postgres(impatient, 5)
        assert sqlstate(caught.value) == "57014"  # query_canceled
        await touch(impatient)  # the session survives the cancelled statement


async def lock_every_note(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(select(notes.c.id).with_for_update())


@pytest.fixture
async def contended(database: Database) -> AsyncGenerator[None]:
    """``notes`` with one row, dropped after the test."""
    async with database.engine.begin() as connection:
        await connection.run_sync(notes.create)
        await connection.execute(notes.insert().values(id=new_id(), body="contended"))
    yield
    async with database.engine.begin() as connection:
        await connection.run_sync(notes.drop)


@pytest.mark.usefixtures("contended")
async def test_a_statement_that_waits_too_long_for_a_lock_is_cancelled(database_url: str) -> None:
    limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=100))
    async with (
        Database.open(database_url, pool_size=2, limits=limits) as impatient,
        impatient.engine.begin() as holder,
    ):
        await holder.execute(select(notes.c.id).with_for_update())
        with pytest.raises(DBAPIError) as caught:
            await lock_every_note(impatient)
        assert sqlstate(caught.value) == "55P03"  # lock_not_available


async def stall_in_a_transaction(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(text("SELECT 1"))
        await asyncio.sleep(0.5)
        await connection.execute(text("SELECT 1"))


async def test_a_session_idle_in_a_transaction_is_ended_by_postgresql(database_url: str) -> None:
    limits = DatabaseLimits(idle_in_transaction_session_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=limits) as impatient:
        with pytest.raises(DBAPIError):
            await stall_in_a_transaction(impatient)
        await touch(impatient)  # the pool replaced the connection that PostgreSQL ended


async def open_it(url: str, *, pool_size: int = 1) -> None:
    async with Database.open(url, pool_size=pool_size):
        pass


@pytest.mark.parametrize("pool_size", [0, -3])
async def test_a_pool_needs_at_least_one_connection(database_url: str, pool_size: int) -> None:
    with pytest.raises(InvalidOptionError, match=f"pool_size must be at least 1, not int: {pool_size}"):
        await open_it(database_url, pool_size=pool_size)


async def test_a_pool_without_overflow_grows_no_further_than_its_size(database_url: str) -> None:
    limits = DatabaseLimits(pool_timeout=timedelta(milliseconds=100))
    exact = Database.create(database_url, pool_size=1, limits=limits, pooling=Pooling(overflow=0))
    try:
        async with exact.engine.connect():
            with pytest.raises(PoolTimeoutError):
                await asyncio.wait_for(touch(exact), timeout=5)
    finally:
        await exact.aclose()


def test_a_pool_s_overflow_is_never_negative(database_url: str) -> None:
    with pytest.raises(InvalidOptionError, match="overflow must be at least 0, not int: -1"):
        Database.create(database_url, pooling=Pooling(overflow=-1))


async def test_only_the_psycopg_driver_is_accepted_and_the_url_is_not_echoed() -> None:
    with pytest.raises(InvalidOptionError) as caught:
        await open_it("postgresql+asyncpg://someone:secret@db.internal/ledger")
    assert str(caught.value) == (
        "database option url driver must be 'postgresql+psycopg' or 'postgresql+psycopg_async',"
        " not str: postgresql+asyncpg"
    )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "invalid_option")


async def test_an_engine_of_another_driver_is_refused_whoever_builds_the_database() -> None:
    stand_in = SimpleNamespace(
        paramstyle="numeric_dollar"
    )  # asyncpg is not installed; its dialect needs this
    engine = create_async_engine("postgresql+asyncpg://someone:secret@db.internal/ledger", module=stand_in)
    try:
        with pytest.raises(InvalidOptionError, match="url driver must be"):
            Database(engine, probe=engine)
    finally:
        await engine.dispose()


async def test_the_asynchronous_spelling_of_the_driver_is_the_same_driver(database_url: str) -> None:
    spelled = database_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    assert spelled != database_url
    async with Database.open(spelled, pool_size=1) as asynchronous:
        assert await setting(asynchronous, "application_name") == "ledger"


@pytest.mark.parametrize("key", ["application_name", "connect_timeout", "options"])
async def test_the_url_cannot_set_the_connection_parameters_the_chassis_owns(key: str) -> None:
    with pytest.raises(InvalidOptionError) as caught:
        await open_it(f"postgresql+psycopg://someone:secret@db.internal/ledger?{key}=mine")
    assert (
        str(caught.value)
        == f"database option url query {key} must be absent (the chassis sets it), not str: mine"
    )


async def test_other_parameters_of_the_url_are_left_alone(database_url: str) -> None:
    async with Database.open(f"{database_url}?sslmode=disable", pool_size=1) as plain:
        assert (await libpq_parameters(plain))["sslmode"] == "disable"


@pytest.mark.parametrize("option", ["connect_timeout", "pool_recycle"])
@pytest.mark.parametrize(
    "value",
    [timedelta(0), timedelta(milliseconds=999), timedelta(seconds=-1)],
    ids=["zero", "sub-second", "negative"],
)
def test_a_limit_in_whole_seconds_needs_at_least_one_second(option: str, value: timedelta) -> None:
    with pytest.raises(
        InvalidOptionError,
        match=f"{option} must be between one second and 2147483647 milliseconds, not timedelta",
    ):
        DatabaseLimits(**{option: value})


@pytest.mark.parametrize(
    "option", ["statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "pool_timeout"]
)
@pytest.mark.parametrize(
    "value",
    [timedelta(0), timedelta(microseconds=999), timedelta(seconds=-1)],
    ids=["zero", "sub-millisecond", "negative"],
)
def test_a_limit_needs_at_least_a_millisecond(option: str, value: timedelta) -> None:
    with pytest.raises(
        InvalidOptionError,
        match=f"{option} must be between one millisecond and 2147483647 milliseconds, not timedelta",
    ):
        DatabaseLimits(**{option: value})


LIMITS = [
    "connect_timeout",
    "statement_timeout",
    "lock_timeout",
    "idle_in_transaction_session_timeout",
    "pool_timeout",
    "pool_recycle",
]
LONGEST = timedelta(milliseconds=2**31 - 1)
"""PostgreSQL keeps these settings in 32-bit milliseconds; one more and every connection fails."""


@pytest.mark.parametrize("option", LIMITS)
def test_a_limit_beyond_32_bit_milliseconds_is_refused(option: str) -> None:
    with pytest.raises(InvalidOptionError, match=f"{option} must be between .* and 2147483647 milliseconds"):
        DatabaseLimits(**{option: LONGEST + timedelta(milliseconds=1)})


def test_the_longest_limits_are_accepted() -> None:
    DatabaseLimits(**dict.fromkeys(LIMITS, LONGEST))


async def test_postgresql_accepts_the_longest_limits(database_url: str) -> None:
    limits = DatabaseLimits(
        statement_timeout=LONGEST, lock_timeout=LONGEST, idle_in_transaction_session_timeout=LONGEST
    )
    async with Database.open(database_url, pool_size=1, limits=limits) as longest:
        shown = [
            await setting(longest, name)
            for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
        ]
    assert shown == ["2147483647ms"] * 3


def test_the_smallest_limits_are_accepted() -> None:
    DatabaseLimits(
        connect_timeout=timedelta(seconds=1),
        statement_timeout=timedelta(milliseconds=1),
        lock_timeout=timedelta(milliseconds=1),
        idle_in_transaction_session_timeout=timedelta(milliseconds=1),
        pool_timeout=timedelta(milliseconds=1),
        pool_recycle=timedelta(seconds=1),
    )


# ── DDL ─────────────────────────────────────────────────────────────────────────
