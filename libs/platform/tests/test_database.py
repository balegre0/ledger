import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from functools import partial
from types import SimpleNamespace
lazy from collections.abc import AsyncGenerator, Callable, Iterable
lazy from uuid import UUID

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
    delete,
    func,
    insert,
    literal_column,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import QueuePool
from structlog.testing import capture_logs
lazy from sqlalchemy import Insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.database import (
    AbortedTransactionError,
    ClosedUnitOfWorkError,
    Database,
    DatabaseLimits,
    InvalidDatabaseOptionError,
    MissingTenantColumnError,
    tenant_isolation,
    tenant_isolation_removal,
)
lazy from ledger_platform.database import AnyTable, UnitOfWork

metadata = MetaData()
notes = Table(
    "notes",
    metadata,
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),
    Column("company_id", Uuid(), nullable=False),
    Column("body", Text, nullable=False),
)
ORGANIZATION = OrganizationId(new_id())
OTHER_ORGANIZATION = OrganizationId(new_id())
ALPHA = Tenant(ORGANIZATION, CompanyId(new_id()))
BETA = Tenant(ORGANIZATION, CompanyId(new_id()))
UNREACHABLE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


@asynccontextmanager
async def created(database: Database, table: AnyTable) -> AsyncGenerator[None]:
    """``table`` created, and dropped on exit so that the next test starts clean."""
    async with database.engine.begin() as connection:
        await connection.run_sync(table.create)
    try:
        yield
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(table.drop)


async def apply(database: Database, statements: Iterable[str]) -> None:
    async with database.engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


@asynccontextmanager
async def isolating(database: Database, table: AnyTable) -> AsyncGenerator[None]:
    """``table`` created and put under ``tenant_isolation``."""
    async with created(database, table):
        await apply(database, tenant_isolation(table))
        yield


@pytest.fixture
async def isolated(database: Database) -> AsyncGenerator[Database]:
    async with isolating(database, notes):
        yield database


def note(owner: Tenant, body: str, *, row: UUID | None = None) -> Insert:
    return insert(notes).values(
        id=row or new_id(), organization_id=owner.organization, company_id=owner.company, body=body
    )


async def write(database: Database, tenant: Tenant | None, body: str) -> None:
    async with database.transaction(tenant) as unit:
        await unit.connection.execute(note(tenant or ALPHA, body))


async def read(database: Database, tenant: Tenant | None) -> list[str]:
    async with database.transaction(tenant) as unit:
        return list((await unit.connection.execute(select(notes.c.body).order_by(notes.c.body))).scalars())


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
    """How many sessions PostgreSQL holds for ``application_name``."""
    named = (
        select(func.count())
        .select_from(text("pg_stat_activity"))
        .where(column("application_name", Text) == application_name)
    )
    async with database.transaction(None) as unit:
        return (await unit.connection.execute(named)).scalar_one()


async def setting(database: Database, name: str) -> str:
    async with database.transaction(None) as unit:
        shown = literal_column(f"current_setting('{name}')", Text)
        return (await unit.connection.execute(select(shown))).scalar_one()


# ── Row-level security ──────────────────────────────────────────────────────────


async def test_each_company_sees_only_its_rows(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    await write(isolated, BETA, "beta 1")
    assert await read(isolated, ALPHA) == ["alpha 1"]
    assert await read(isolated, BETA) == ["beta 1"]


async def test_without_a_tenant_nothing_is_visible_or_writable(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    assert await read(isolated, None) == []
    with pytest.raises(DBAPIError, match="row-level security"):
        await write(isolated, None, "orphan")


async def test_a_company_cannot_write_rows_of_another(isolated: Database) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with isolated.transaction(ALPHA) as unit:
            await unit.connection.execute(note(BETA, "x"))


async def test_a_company_cannot_write_into_another_organization(isolated: Database) -> None:
    stranger = Tenant(OTHER_ORGANIZATION, ALPHA.company)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with isolated.transaction(stranger) as unit:
            await unit.connection.execute(note(ALPHA, "x"))


async def test_the_same_company_under_another_organization_sees_nothing(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    assert await read(isolated, Tenant(OTHER_ORGANIZATION, ALPHA.company)) == []


async def test_a_company_cannot_hand_its_row_to_another(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with isolated.transaction(ALPHA) as unit:
            await unit.connection.execute(update(notes).values(company_id=BETA.company))
    assert await read(isolated, ALPHA) == ["alpha 1"]


async def test_a_company_cannot_change_or_delete_rows_of_another(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    async with isolated.transaction(BETA) as unit:
        changed = await unit.connection.execute(update(notes).values(body="defaced"))
        removed = await unit.connection.execute(delete(notes))
    assert (changed.rowcount, removed.rowcount) == (0, 0)
    assert await read(isolated, ALPHA) == ["alpha 1"]


async def test_the_tenant_does_not_leak_into_the_next_transaction(database_url: str) -> None:
    """One pooled connection, so the next user of the pool is handed exactly the one the tenant just used."""
    async with Database.open(database_url, pool_size=1) as single, isolating(single, notes):
        await write(single, ALPHA, "alpha 1")
        async with single.transaction(ALPHA) as unit:
            used = await backend_of(unit.connection)
        async with single.engine.connect() as inheritor:  # not transaction(): it sets nothing itself
            company = literal_column("current_setting('ledger.company_id', true)", Text)
            left_behind = (await inheritor.execute(select(company))).scalar_one()
            visible = list((await inheritor.execute(select(notes.c.body))).scalars())
            assert await backend_of(inheritor) == used
    assert not left_behind  # empty, or never set
    assert visible == []


async def test_concurrent_transactions_each_keep_their_own_tenant(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    await write(isolated, BETA, "beta 1")
    both_are_open = asyncio.Barrier(2)

    async def read_while_the_other_is_open(tenant: Tenant) -> list[str]:
        async with isolated.transaction(tenant) as unit:
            await both_are_open.wait()
            return list((await unit.connection.execute(select(notes.c.body))).scalars())

    async with asyncio.TaskGroup() as group:
        alpha = group.create_task(read_while_the_other_is_open(ALPHA))
        beta = group.create_task(read_while_the_other_is_open(BETA))
    assert (alpha.result(), beta.result()) == (["alpha 1"], ["beta 1"])


async def test_the_owner_of_a_table_is_held_to_its_policy(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    async with isolated.engine.connect() as connection:  # no transaction(): no tenant was ever set
        assert list((await connection.execute(select(notes.c.body))).scalars()) == []


async def test_the_transaction_carries_the_tenant(database: Database) -> None:
    organization = literal_column("current_setting('ledger.organization_id')", Text)
    company = literal_column("current_setting('ledger.company_id')", Text)
    async with database.transaction(ALPHA) as unit:
        carried = (await unit.connection.execute(select(organization, company))).one()
    assert carried == (str(ORGANIZATION), str(ALPHA.company))


# ── A commit is a commit ────────────────────────────────────────────────────────


async def swallow_a_failed_statement(database: Database, calls: list[str], *, in_a_savepoint: bool) -> None:
    """What a handler that catches a database error does to its transaction, with or without a savepoint."""
    row = new_id()
    async with database.transaction(ALPHA) as unit:
        unit.after_commit(lambda: calls.append("committed"))
        await unit.connection.execute(note(ALPHA, "kept?", row=row))
        with suppress(IntegrityError):
            if in_a_savepoint:
                async with unit.connection.begin_nested():
                    await unit.connection.execute(note(ALPHA, "duplicate", row=row))
            else:
                await unit.connection.execute(note(ALPHA, "duplicate", row=row))


async def test_a_swallowed_database_error_cannot_be_committed(isolated: Database) -> None:
    calls: list[str] = []
    with pytest.raises(AbortedTransactionError, match="rolled back, not committed") as caught:
        await swallow_a_failed_statement(isolated, calls, in_a_savepoint=False)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "aborted_transaction")
    assert calls == []
    assert await read(isolated, ALPHA) == []


async def test_an_error_swallowed_inside_a_savepoint_leaves_the_transaction_whole(isolated: Database) -> None:
    calls: list[str] = []
    await swallow_a_failed_statement(isolated, calls, in_a_savepoint=True)
    assert calls == ["committed"]
    assert await read(isolated, ALPHA) == ["kept?"]


async def write_then_fail(database: Database, calls: list[str]) -> None:
    async with database.transaction(ALPHA) as unit:
        unit.after_commit(lambda: calls.append("rolled back"))
        await unit.connection.execute(note(ALPHA, "gone"))
        raise LookupError


async def test_after_commit_callbacks_run_on_commit(isolated: Database) -> None:
    calls: list[str] = []
    async with isolated.transaction(ALPHA) as unit:
        unit.after_commit(lambda: calls.append("committed"))
    assert calls == ["committed"]


async def test_after_commit_callbacks_run_after_the_transaction_has_ended(database: Database) -> None:
    open_while: list[bool] = []
    async with database.transaction(ALPHA) as unit:
        open_while.append(unit.connection.in_transaction())
        unit.after_commit(lambda: open_while.append(unit.connection.in_transaction()))
    assert open_while == [True, False]


async def test_after_commit_callbacks_are_dropped_on_rollback(isolated: Database) -> None:
    calls: list[str] = []
    with pytest.raises(LookupError):
        await write_then_fail(isolated, calls)
    assert calls == []
    assert await read(isolated, ALPHA) == []


async def test_a_callback_added_after_the_commit_is_refused(isolated: Database) -> None:
    async with isolated.transaction(ALPHA) as unit:
        pass
    with pytest.raises(ClosedUnitOfWorkError, match="the transaction has ended") as caught:
        unit.after_commit(explode)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "closed_unit_of_work")


async def keep_the_unit_and_fail(database: Database, kept: list[UnitOfWork]) -> None:
    async with database.transaction(ALPHA) as unit:
        kept.append(unit)
        raise LookupError


async def test_a_callback_added_after_a_rollback_is_refused(isolated: Database) -> None:
    kept: list[UnitOfWork] = []
    with pytest.raises(LookupError):
        await keep_the_unit_and_fail(isolated, kept)
    with pytest.raises(ClosedUnitOfWorkError):
        kept[0].after_commit(explode)


async def test_a_callback_cannot_add_a_callback(isolated: Database) -> None:
    refused: list[ClosedUnitOfWorkError] = []
    async with isolated.transaction(ALPHA) as unit:

        def add_another() -> None:
            try:
                unit.after_commit(explode)
            except ClosedUnitOfWorkError as error:
                refused.append(error)

        unit.after_commit(add_another)
    assert [(error.kind, error.code) for error in refused] == [(ErrorKind.INTERNAL, "closed_unit_of_work")]


async def test_after_commit_callbacks_run_in_the_order_they_were_added(database: Database) -> None:
    calls: list[str] = []
    async with database.transaction(None) as unit:
        unit.after_commit(lambda: calls.append("first"))
        unit.after_commit(lambda: calls.append("second"))
        assert calls == []
    assert calls == ["first", "second"]


def explode() -> None:
    raise LookupError


def explode_because(reason: str) -> None:
    raise LookupError(reason)


class Exploder:
    def __call__(self) -> None:
        raise LookupError

    def method(self) -> None:
        raise LookupError


@pytest.mark.parametrize(
    ("failing", "named"),
    [
        (explode, f"{__name__}.explode"),
        (Exploder().method, f"{__name__}.Exploder.method"),
        (list[None]().pop, "builtins.list.pop"),
        (partial(explode_because, "why"), f"partial({__name__}.explode_because)"),
        (Exploder(), f"{__name__}.Exploder"),
    ],
    ids=["function", "method", "builtin method", "partial", "callable object"],
)
async def test_a_failing_callback_neither_hides_the_commit_nor_stops_the_others(
    isolated: Database, failing: Callable[[], None], named: str
) -> None:
    calls: list[str] = []
    with capture_logs() as logs:
        async with isolated.transaction(ALPHA) as unit:
            unit.after_commit(lambda: calls.append("first"))
            unit.after_commit(failing)
            unit.after_commit(lambda: calls.append("last"))
            await unit.connection.execute(note(ALPHA, "kept"))
    assert calls == ["first", "last"]
    assert await read(isolated, ALPHA) == ["kept"]
    assert logs == [
        {"event": "database.after_commit_failed", "log_level": "error", "callback": named, "exc_info": True}
    ]


# ── Ping and the pool ───────────────────────────────────────────────────────────


BOUND_VALUE = "a-value-that-must-not-leak"


async def fail_on_a_missing_table(database: Database, value: str) -> None:
    async with database.transaction(ALPHA) as unit:
        await unit.connection.execute(text("SELECT * FROM no_such_table WHERE name = :name"), {"name": value})


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
    async with database.transaction(None):
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
        async with pooled.transaction(None) as unit:
            ended = await backend_of(unit.connection)
        async with database.transaction(None) as admin:
            terminated = literal_column(
                f"pg_terminate_backend({ended}, 5000)", Boolean
            )  # waits until it is gone
            assert (await admin.connection.execute(select(terminated))).scalar_one()
        async with pooled.transaction(None) as unit:
            replacement = await backend_of(unit.connection)
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
        async with recycling.transaction(None) as unit:
            first = await backend_of(unit.connection)
        async with recycling.transaction(None) as unit:
            soon_after = await backend_of(unit.connection)
        await asyncio.sleep(1.2)
        async with recycling.transaction(None) as unit:
            much_later = await backend_of(unit.connection)
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
    async with database.transaction(None) as unit:
        await unit.connection.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": seconds})


async def test_a_statement_that_outlives_the_statement_timeout_is_cancelled(database_url: str) -> None:
    limits = DatabaseLimits(statement_timeout=timedelta(milliseconds=100))
    async with Database.open(database_url, pool_size=1, limits=limits) as impatient:
        with pytest.raises(DBAPIError) as caught:
            await sleep_in_postgres(impatient, 5)
        assert sqlstate(caught.value) == "57014"  # query_canceled
        await touch(impatient)  # the session survives the cancelled statement


async def lock_every_note(database: Database) -> None:
    async with database.transaction(ALPHA) as unit:
        await unit.connection.execute(select(notes.c.id).with_for_update())


async def test_a_statement_that_waits_too_long_for_a_lock_is_cancelled(database_url: str) -> None:
    limits = DatabaseLimits(lock_timeout=timedelta(milliseconds=100))
    async with (
        Database.open(database_url, pool_size=2, limits=limits) as impatient,
        isolating(impatient, notes),
    ):
        await write(impatient, ALPHA, "contended")
        async with impatient.transaction(ALPHA) as holder:
            await holder.connection.execute(select(notes.c.id).with_for_update())
            with pytest.raises(DBAPIError) as caught:
                await lock_every_note(impatient)
            assert sqlstate(caught.value) == "55P03"  # lock_not_available


async def stall_in_a_transaction(database: Database) -> None:
    async with database.transaction(None) as unit:
        await unit.connection.execute(text("SELECT 1"))
        await asyncio.sleep(0.5)
        await unit.connection.execute(text("SELECT 1"))


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
    with pytest.raises(
        InvalidDatabaseOptionError, match=f"pool_size must be at least 1, not int: {pool_size}"
    ):
        await open_it(database_url, pool_size=pool_size)


async def test_only_the_psycopg_driver_is_accepted_and_the_url_is_not_echoed() -> None:
    with pytest.raises(InvalidDatabaseOptionError) as caught:
        await open_it("postgresql+asyncpg://someone:secret@db.internal/ledger")
    assert str(caught.value) == (
        "database option url driver must be 'postgresql+psycopg' or 'postgresql+psycopg_async',"
        " not str: postgresql+asyncpg"
    )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "invalid_database_option")


async def test_an_engine_of_another_driver_is_refused_whoever_builds_the_database() -> None:
    stand_in = SimpleNamespace(
        paramstyle="numeric_dollar"
    )  # asyncpg is not installed; its dialect needs this
    engine = create_async_engine("postgresql+asyncpg://someone:secret@db.internal/ledger", module=stand_in)
    try:
        with pytest.raises(InvalidDatabaseOptionError, match="url driver must be"):
            Database(engine, probe=engine)
    finally:
        await engine.dispose()


async def test_the_asynchronous_spelling_of_the_driver_is_the_same_driver(database_url: str) -> None:
    spelled = database_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    assert spelled != database_url
    async with Database.open(spelled, pool_size=1) as asynchronous:
        assert (
            await setting(asynchronous, "application_name") == "ledger"
        )  # a transaction, so a checked commit


@pytest.mark.parametrize("key", ["application_name", "connect_timeout", "options"])
async def test_the_url_cannot_set_the_connection_parameters_the_chassis_owns(key: str) -> None:
    with pytest.raises(InvalidDatabaseOptionError) as caught:
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
        InvalidDatabaseOptionError,
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
        InvalidDatabaseOptionError,
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
    with pytest.raises(
        InvalidDatabaseOptionError, match=f"{option} must be between .* and 2147483647 milliseconds"
    ):
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


def table_named(name: str, *, schema: str | None = None) -> AnyTable:
    return Table(
        name,
        MetaData(),
        Column("id", Uuid(), primary_key=True),
        Column("organization_id", Uuid(), nullable=False),
        Column("company_id", Uuid(), nullable=False),
        schema=schema,
    )


@pytest.mark.parametrize(
    ("columns", "missing"),
    [
        (("id", "company_id"), "organization_id"),
        (("id", "organization_id"), "company_id"),
        (("id",), "organization_id, company_id"),
    ],
    ids=["no organization", "no company", "neither"],
)
def test_isolation_names_the_tenant_columns_a_table_lacks(columns: tuple[str, ...], missing: str) -> None:
    other = Table("other", MetaData(), *(Column(name, Uuid()) for name in columns))
    with pytest.raises(MissingTenantColumnError) as caught:
        tenant_isolation(other)
    assert str(caught.value) == f"table 'other' is missing the tenant columns {missing}"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "missing_tenant_column")


def test_the_policy_binds_the_organization_and_the_company_in_both_directions() -> None:
    enable, force, drop, policy = tenant_isolation(table_named("notes"))
    assert enable == "ALTER TABLE notes ENABLE ROW LEVEL SECURITY"
    assert force == "ALTER TABLE notes FORCE ROW LEVEL SECURITY"
    assert drop == "DROP POLICY IF EXISTS tenant_isolation ON notes"
    rule = (
        "organization_id = NULLIF(current_setting('ledger.organization_id', true), '')::uuid"
        " AND company_id = NULLIF(current_setting('ledger.company_id', true), '')::uuid"
    )
    assert policy == f"CREATE POLICY tenant_isolation ON notes USING ({rule}) WITH CHECK ({rule})"


def test_the_ddl_quotes_the_names_that_need_it() -> None:
    enable, force, drop, policy = tenant_isolation(table_named('Say "hi"', schema="Own Schema"))
    assert enable == 'ALTER TABLE "Own Schema"."Say ""hi""" ENABLE ROW LEVEL SECURITY'
    assert force == 'ALTER TABLE "Own Schema"."Say ""hi""" FORCE ROW LEVEL SECURITY'
    assert drop == 'DROP POLICY IF EXISTS tenant_isolation ON "Own Schema"."Say ""hi"""'
    assert policy.startswith('CREATE POLICY tenant_isolation ON "Own Schema"."Say ""hi""" USING (')


def test_the_removal_undoes_the_isolation_step_by_step() -> None:
    assert tenant_isolation_removal(table_named("notes")) == (
        "DROP POLICY IF EXISTS tenant_isolation ON notes",
        "ALTER TABLE notes NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE notes DISABLE ROW LEVEL SECURITY",
    )
    assert tenant_isolation_removal(table_named('Say "hi"', schema="Own Schema"))[1] == (
        'ALTER TABLE "Own Schema"."Say ""hi""" NO FORCE ROW LEVEL SECURITY'
    )


async def test_isolation_can_be_applied_again(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    await apply(isolated, tenant_isolation(notes))
    assert await read(isolated, ALPHA) == ["alpha 1"]
    assert await read(isolated, BETA) == []
    assert await read(isolated, None) == []


async def test_removing_the_isolation_opens_the_table_and_it_can_be_put_back(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    await write(isolated, BETA, "beta 1")
    await apply(isolated, tenant_isolation_removal(notes))
    assert await read(isolated, None) == ["alpha 1", "beta 1"]
    await apply(isolated, tenant_isolation(notes))
    assert await read(isolated, None) == []
    assert await read(isolated, BETA) == ["beta 1"]


async def test_removing_the_isolation_of_a_table_without_it_is_harmless(database: Database) -> None:
    async with created(database, notes):
        await apply(database, tenant_isolation_removal(notes))
        await write(database, ALPHA, "alpha 1")
        assert await read(database, None) == ["alpha 1"]


@pytest.mark.parametrize("name", ["Mixed Case", 'say "hi"', "select", "semi;colon -- not a comment"])
async def test_a_table_named_oddly_is_isolated_all_the_same(database: Database, name: str) -> None:
    oddly_named = table_named(name)
    row = insert(oddly_named).values(
        id=new_id(), organization_id=ALPHA.organization, company_id=ALPHA.company
    )
    async with isolating(database, oddly_named):
        async with database.transaction(ALPHA) as unit:
            await unit.connection.execute(row)
        async with database.transaction(ALPHA) as unit:
            alpha = (await unit.connection.execute(select(oddly_named.c.id))).all()
        async with database.transaction(BETA) as unit:
            beta = (await unit.connection.execute(select(oddly_named.c.id))).all()
    assert (len(alpha), len(beta)) == (1, 0)
