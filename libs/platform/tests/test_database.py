import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
lazy from collections.abc import AsyncGenerator, Callable, Iterable
lazy from uuid import UUID

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    delete,
    insert,
    literal_column,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.pool import QueuePool
from structlog.testing import capture_logs
lazy from sqlalchemy import Insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.database import (
    AbortedTransactionError,
    Database,
    DatabaseLimits,
    InvalidDatabaseOptionError,
    MissingTenantColumnError,
    tenant_isolation,
    tenant_isolation_removal,
)
lazy from ledger_platform.database import AnyTable

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


async def test_after_commit_callbacks_run_in_the_order_they_were_added(database: Database) -> None:
    calls: list[str] = []
    async with database.transaction(None) as unit:
        unit.after_commit(lambda: calls.append("first"))
        unit.after_commit(lambda: calls.append("second"))
        assert calls == []
    assert calls == ["first", "second"]


def explode() -> None:
    raise LookupError


class Exploder:
    def __call__(self) -> None:
        raise LookupError


@pytest.mark.parametrize(
    ("failing", "named"),
    [(explode, f"{__name__}.explode"), (Exploder(), "Exploder")],
    ids=["function", "callable object"],
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


async def test_ping(database: Database) -> None:
    await database.ping()


async def test_ping_fails_when_postgresql_cannot_serve_the_database(database_url: str) -> None:
    absent = make_url(database_url).set(database="absent").render_as_string(hide_password=False)
    async with Database.open(absent, pool_size=1) as unserved:
        with pytest.raises(OperationalError, match='"absent" does not exist'):
            await unserved.ping()


async def test_leaving_the_context_closes_the_pool(database_url: str) -> None:
    async with Database.open(database_url, pool_size=1) as opened:
        await opened.ping()
        pool = opened.engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.checkedin() == 1
    assert pool.checkedin() == 0


async def test_the_pool_is_closed_when_the_body_fails(database_url: str) -> None:
    pools: list[QueuePool] = []

    async def ping_then_fail() -> None:
        async with Database.open(database_url, pool_size=1) as opened:
            await opened.ping()
            pool = opened.engine.pool
            assert isinstance(pool, QueuePool)
            pools.append(pool)
            raise LookupError

    with pytest.raises(LookupError):
        await ping_then_fail()
    assert pools[0].checkedin() == 0


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
        with pytest.raises(PoolTimeoutError, match=r"timed out, timeout 0\.10"):
            await small.ping()


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


async def test_a_server_that_does_not_answer_is_given_up_on_after_the_connect_timeout() -> None:
    """A closed port: libpq alone keeps retrying it on Windows for over two minutes."""
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
        with pytest.raises(DBAPIError, match="statement timeout"):
            await sleep_in_postgres(impatient, 5)
        await impatient.ping()  # the session survives the cancelled statement


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
            with pytest.raises(DBAPIError, match="lock timeout"):
                await lock_every_note(impatient)


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
        await impatient.ping()  # the pool replaced the connection that PostgreSQL ended


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
        "database option url driver must be 'postgresql+psycopg', not str: postgresql+asyncpg"
    )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "invalid_database_option")


@pytest.mark.parametrize("option", ["connect_timeout", "pool_recycle"])
@pytest.mark.parametrize(
    "value",
    [timedelta(0), timedelta(milliseconds=999), timedelta(seconds=-1)],
    ids=["zero", "sub-second", "negative"],
)
def test_a_limit_in_whole_seconds_needs_at_least_one_second(option: str, value: timedelta) -> None:
    with pytest.raises(
        InvalidDatabaseOptionError, match=f"{option} must be at least one second, not timedelta"
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
        InvalidDatabaseOptionError, match=f"{option} must be at least one millisecond, not timedelta"
    ):
        DatabaseLimits(**{option: value})


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
        (("id",), "organization_id and company_id"),
    ],
    ids=["no organization", "no company", "neither"],
)
def test_isolation_names_the_tenant_columns_a_table_lacks(columns: tuple[str, ...], missing: str) -> None:
    other = Table("other", MetaData(), *(Column(name, Uuid()) for name in columns))
    with pytest.raises(MissingTenantColumnError) as caught:
        tenant_isolation(other)
    assert str(caught.value) == f"table 'other' has no {missing} column to isolate by"
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
