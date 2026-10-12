import asyncio
from contextlib import asynccontextmanager
lazy from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import (
    Column,
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
from sqlalchemy.exc import DBAPIError, OperationalError

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform.database import Database, MissingTenantColumnError, tenant_isolation
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
ALPHA = Tenant(ORGANIZATION, CompanyId(new_id()))
BETA = Tenant(ORGANIZATION, CompanyId(new_id()))


@asynccontextmanager
async def isolating(database: Database, table: AnyTable) -> AsyncGenerator[None]:
    """``table`` created and put under ``tenant_isolation``, dropped on exit so the next test starts clean."""
    async with database.engine.begin() as connection:
        await connection.run_sync(table.create)
        for statement in tenant_isolation(table):
            await connection.execute(text(statement))
    try:
        yield
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(table.drop)


@pytest.fixture
async def isolated(database: Database) -> AsyncGenerator[Database]:
    async with isolating(database, notes):
        yield database


async def write(database: Database, tenant: Tenant | None, body: str) -> None:
    async with database.transaction(tenant) as unit:
        owner = tenant or ALPHA
        await unit.connection.execute(
            insert(notes).values(
                id=new_id(), organization_id=owner.organization, company_id=owner.company, body=body
            )
        )


async def read(database: Database, tenant: Tenant | None) -> list[str]:
    async with database.transaction(tenant) as unit:
        return list((await unit.connection.execute(select(notes.c.body).order_by(notes.c.body))).scalars())


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
            await unit.connection.execute(
                insert(notes).values(
                    id=new_id(), organization_id=ORGANIZATION, company_id=BETA.company, body="x"
                )
            )


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


async def test_the_tenant_does_not_leak_into_the_next_transaction(isolated: Database) -> None:
    await write(isolated, ALPHA, "alpha 1")
    assert await read(isolated, ALPHA) == ["alpha 1"]
    assert await read(isolated, None) == []


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


async def write_then_fail(database: Database, calls: list[str]) -> None:
    async with database.transaction(ALPHA) as unit:
        unit.after_commit(lambda: calls.append("rolled back"))
        await unit.connection.execute(
            insert(notes).values(
                id=new_id(), organization_id=ORGANIZATION, company_id=ALPHA.company, body="gone"
            )
        )
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


async def test_ping(database: Database) -> None:
    await database.ping()


async def test_leaving_the_context_closes_the_pool(database_url: str) -> None:
    async with Database.open(database_url, pool_size=1) as opened:
        await opened.ping()
        assert "Connections in pool: 1" in opened.engine.pool.status()
    assert "Connections in pool: 0" in opened.engine.pool.status()


async def test_the_pool_is_closed_when_the_body_fails(database_url: str) -> None:
    opened: list[Database] = []

    async def ping_then_fail() -> None:
        async with Database.open(database_url, pool_size=1) as database:
            opened.append(database)
            await database.ping()
            raise LookupError

    with pytest.raises(LookupError):
        await ping_then_fail()
    assert "Connections in pool: 0" in opened[0].engine.pool.status()


async def test_ping_fails_when_postgresql_cannot_serve_the_database(database_url: str) -> None:
    """Not a closed port: libpq retries a refused connection on Windows for over two minutes."""
    absent = make_url(database_url).set(database="absent").render_as_string(hide_password=False)
    async with Database.open(absent, pool_size=1) as unserved:
        with pytest.raises(OperationalError, match='"absent" does not exist'):
            await unserved.ping()


def test_isolation_needs_a_company_column() -> None:
    other = Table("other", MetaData(), Column("id", Uuid(), primary_key=True))
    with pytest.raises(MissingTenantColumnError, match="other") as caught:
        tenant_isolation(other)
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INTERNAL, "missing_tenant_column")


def table_named(name: str, *, schema: str | None = None) -> AnyTable:
    return Table(
        name,
        MetaData(),
        Column("id", Uuid(), primary_key=True),
        Column("company_id", Uuid(), nullable=False),
        schema=schema,
    )


def test_the_ddl_quotes_the_names_that_need_it() -> None:
    enable, force, policy = tenant_isolation(table_named('Say "hi"', schema="Own Schema"))
    assert enable == 'ALTER TABLE "Own Schema"."Say ""hi""" ENABLE ROW LEVEL SECURITY'
    assert force == 'ALTER TABLE "Own Schema"."Say ""hi""" FORCE ROW LEVEL SECURITY'
    assert policy.startswith('CREATE POLICY tenant_isolation ON "Own Schema"."Say ""hi""" USING (')


def test_the_ddl_leaves_a_plain_name_bare() -> None:
    assert tenant_isolation(table_named("notes"))[0] == "ALTER TABLE notes ENABLE ROW LEVEL SECURITY"


@pytest.mark.parametrize("name", ["Mixed Case", 'say "hi"', "select", "semi;colon -- not a comment"])
async def test_a_table_named_oddly_is_isolated_all_the_same(database: Database, name: str) -> None:
    oddly_named = table_named(name)
    async with isolating(database, oddly_named):
        async with database.transaction(ALPHA) as unit:
            await unit.connection.execute(insert(oddly_named).values(id=new_id(), company_id=ALPHA.company))
        async with database.transaction(ALPHA) as unit:
            alpha = (await unit.connection.execute(select(oddly_named.c.id))).all()
        async with database.transaction(BETA) as unit:
            beta = (await unit.connection.execute(select(oddly_named.c.id))).all()
    assert (len(alpha), len(beta)) == (1, 0)
