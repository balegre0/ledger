"""The platform's migration runner, on scripts of a test service: two revisions, the second a backfill."""

import asyncio
from datetime import timedelta
from typing import Final
lazy from collections.abc import Iterator
lazy from pathlib import Path

import psycopg
import pytest
from alembic import command
from pydantic import TypeAdapter
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    create_engine,
    inspect,
    literal_column,
    text,
    update,
)
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection

from ledger_kernel.errors import ErrorKind
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_platform import migrations
from ledger_platform.data.database import (
    SET_SCOPE,
    Database,
    DatabaseLimits,
    organization_isolation,
    scope_settings,
    tenant_isolation,
)
from ledger_platform.migrations import (
    MIGRATION_LOCK_WAIT,
    InvalidMigrationOptionError,
    MigrationLockTimeoutError,
    Migrations,
    MigrationUrlMissingError,
    NoIsolationPolicyError,
    OfflineMigrationsError,
    SchemaNotCurrentError,
    Target,
    UnexpectedRowCountError,
    rows_by_scope,
    run_per_scope,
)
from ledger_testing.containers import libpq_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_platform.data.database import Scope
lazy from ledger_platform.migrations import SchemaReadiness

ENV: Final = """
from sqlalchemy import MetaData

from ledger_platform.migrations import run_migrations

run_migrations(MetaData())
"""
FIRST: Final = '''
"""Notes of each company, under row-level security."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    rule = (
        "organization_id = NULLIF(current_setting('ledger.organization_id', true), '')::uuid "
        "AND company_id = NULLIF(current_setting('ledger.company_id', true), '')::uuid"
    )
    op.execute("ALTER TABLE notes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notes FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON notes USING ({rule}) WITH CHECK ({rule})")


def downgrade() -> None:
    op.drop_table("notes")
'''
SECOND: Final = '''
"""Each note's length, backfilled in each company's scope."""
import sqlalchemy as sa
from alembic import op

from ledger_platform.migrations import rows_by_scope, run_per_scope

revision = "0002"
down_revision = "0001"

NOTES = sa.Table(
    "notes",
    sa.MetaData(),
    sa.Column("organization_id", sa.Uuid()),
    sa.Column("company_id", sa.Uuid()),
    sa.Column("body", sa.Text()),
    sa.Column("length", sa.Integer()),
)


def upgrade() -> None:
    op.add_column("notes", sa.Column("length", sa.Integer(), nullable=True))
    connection = op.get_bind()
    missing = NOTES.c.length.is_(None)
    expected = rows_by_scope(connection, NOTES, missing)
    backfill = sa.update(NOTES).where(missing).values(length=sa.func.length(NOTES.c.body))
    run_per_scope(connection, backfill, expected)


def downgrade() -> None:
    op.drop_column("notes", "length")
'''
NOTES: Final = Table(
    "notes",
    MetaData(),
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),
    Column("company_id", Uuid(), nullable=False),
    Column("body", Text, nullable=False),
    Column("length", Integer, nullable=True),
)
NOTEBOOKS: Final = Table(
    "notebooks",
    MetaData(),
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),
    Column("title", Text, nullable=True),
)
TAGS: Final = Table(
    "tags",
    MetaData(),
    Column("id", Uuid(), primary_key=True),
    Column("organization_id", Uuid(), nullable=False),
    Column("company_id", Uuid(), nullable=False),  # which company a tag is about; the organization owns it
    Column("label", Text, nullable=False),
    Column("read", Boolean, nullable=True),
)
ORGANIZATION: Final = OrganizationId(new_id())
ALPHA: Final = Tenant(ORGANIZATION, CompanyId(new_id()))
BETA: Final = Tenant(ORGANIZATION, CompanyId(new_id()))
ELSEWHERE: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))
HOLD_THE_MIGRATION_LOCK: Final = "SELECT pg_advisory_lock(%s)"
"""What a migration that takes long looks like to another: the lock held, by a session of the test's own."""
CANCEL_THE_WAITING: Final = (
    "SELECT pg_cancel_backend(pid) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)
FORCED: Final = TypeAdapter(list[tuple[str, bool]])
FORCED_SQL: Final = text(
    """
    SELECT relname::text, relforcerowsecurity FROM pg_class WHERE relname IN ('notes', 'notebooks', 'tags')
    """
)
LENGTHS: Final = TypeAdapter(list[tuple[str, int | None]])


@pytest.fixture
def notes_migrations(tmp_path: Path) -> Migrations:
    """A service's scripts: ``0001`` makes ``notes`` under row-level security, ``0002`` backfills a column."""
    (tmp_path / "versions").mkdir()
    (tmp_path / "env.py").write_text(ENV, encoding="utf-8")
    (tmp_path / "versions" / "r0001_notes.py").write_text(FIRST, encoding="utf-8")
    (tmp_path / "versions" / "r0002_length.py").write_text(SECOND, encoding="utf-8")
    return Migrations(str(tmp_path), owner="notes")


@pytest.fixture
def owner(fresh_database_url: str) -> Iterator[Connection]:
    """The database's owner in a transaction rolled back after the test: what a migration runs as."""
    engine = create_engine(fresh_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection, connection.begin():
            yield connection
    finally:
        engine.dispose()


def tables_of(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def execute(url: str, *statements: str) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def forced(connection: Connection) -> dict[str, bool]:
    rows = connection.execute(FORCED_SQL)
    return dict(FORCED.validate_python([tuple(row) for row in rows]))


async def write(database: Database, tenant: Tenant, body: str) -> None:
    async with database.transaction(tenant) as unit:
        await unit.connection.execute(
            NOTES.insert().values(
                id=new_id(), organization_id=tenant.organization, company_id=tenant.company, body=body
            )
        )


async def lengths(database: Database, tenant: Tenant) -> list[tuple[str, int | None]]:
    async with database.transaction(tenant) as unit:
        rows = await unit.connection.execute(NOTES.select().with_only_columns(NOTES.c.body, NOTES.c.length))
        return sorted(LENGTHS.validate_python([tuple(row) for row in rows]))


# ── Running ─────────────────────────────────────────────────────────────────────


def test_a_schema_is_built_from_nothing_and_taken_back_to_nothing(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    assert (notes_migrations.head(), notes_migrations.revisions()) == ("0002", frozenset({"0001", "0002"}))
    notes_migrations.upgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"notes", "alembic_version"}
    notes_migrations.downgrade(fresh_database_url)
    assert tables_of(fresh_database_url) == {"alembic_version"}


def test_a_migration_waits_for_another_long_but_not_forever() -> None:
    assert timedelta(minutes=10) == MIGRATION_LOCK_WAIT


@pytest.mark.parametrize(
    "wait",
    [timedelta(0), timedelta(milliseconds=-1), timedelta(milliseconds=2**31)],
    ids=["zero", "negative", "beyond-postgresql"],
)
def test_a_migration_waits_a_positive_time_postgresql_can_hold(
    notes_migrations: Migrations, wait: timedelta
) -> None:
    """Zero would be no bound at all to ``lock_timeout``; above 2^31 - 1 ms PostgreSQL refuses the setting."""
    with pytest.raises(InvalidMigrationOptionError) as caught:
        notes_migrations.upgrade("postgresql+psycopg://nobody@localhost/nothing", lock_wait=wait)
    assert caught.value.code == "invalid_migration_option"
    assert str(caught.value).startswith("migration option lock_wait must be between 1 microsecond and ")


def test_the_longest_wait_postgresql_can_hold_is_accepted() -> None:
    longest = timedelta(milliseconds=2**31 - 1)
    target = Target(url="postgresql+psycopg://nobody@localhost/nothing", owner="notes", lock_wait=longest)
    assert target.lock_wait == longest
    assert "nobody" not in repr(target)


async def test_a_migration_that_waits_longer_than_its_bound_fails_saying_why(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    async with await psycopg.AsyncConnection.connect(libpq_url(fresh_database_url), autocommit=True) as other:
        await other.execute(HOLD_THE_MIGRATION_LOCK, (migrations.MIGRATION_LOCK,))
        with pytest.raises(MigrationLockTimeoutError) as caught:
            await asyncio.to_thread(
                notes_migrations.upgrade, fresh_database_url, lock_wait=timedelta(milliseconds=50)
            )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.UNAVAILABLE, "migration_lock_timeout")
    assert (
        str(caught.value)
        == "another migration of notes's database held its lock for longer than 0:00:00.050000"
    )
    assert tables_of(fresh_database_url) == set()  # nothing ran, not even Alembic's own table
    notes_migrations.upgrade(fresh_database_url)  # once the other one is done, the next migration runs
    assert "notes" in tables_of(fresh_database_url)


async def test_a_migration_cancelled_while_it_waits_fails_with_the_database_s_error(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    async with await psycopg.AsyncConnection.connect(libpq_url(fresh_database_url), autocommit=True) as other:
        await other.execute(HOLD_THE_MIGRATION_LOCK, (migrations.MIGRATION_LOCK,))
        waiting = asyncio.create_task(asyncio.to_thread(notes_migrations.upgrade, fresh_database_url))
        await eventually(lambda: waits_for_a_lock(fresh_database_url))
        await other.execute(CANCEL_THE_WAITING)
        with pytest.raises(OperationalError) as caught:
            await waiting
    assert isinstance(caught.value.orig, psycopg.errors.QueryCanceled)


def test_migrations_are_never_written_out_as_sql(notes_migrations: Migrations) -> None:
    with pytest.raises(OfflineMigrationsError, match="run against a database, not as SQL text"):
        command.upgrade(notes_migrations.config(), "head", sql=True)


def test_migrations_need_a_database_to_run_against(notes_migrations: Migrations) -> None:
    with pytest.raises(MigrationUrlMissingError, match="without a database URL"):
        command.upgrade(notes_migrations.config(), "head")


# ── Readiness ───────────────────────────────────────────────────────────────────


async def refused(readiness: SchemaReadiness, url: str) -> SchemaNotCurrentError:
    async with Database.open(url, pool_size=1) as database:
        with pytest.raises(SchemaNotCurrentError) as caught:
            await readiness(database)
    return caught.value


async def passes(readiness: SchemaReadiness, url: str) -> None:
    async with Database.open(url, pool_size=1) as database:
        await readiness(database)


async def test_a_database_never_migrated_is_not_ready(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    error = await refused(notes_migrations.readiness(), fresh_database_url)
    assert (error.kind, error.code) == (ErrorKind.UNAVAILABLE, "schema_not_current")
    assert str(error) == "the database schema is not at revision 0002, the one this version needs"


async def test_a_database_migrated_and_taken_back_to_the_base_is_not_ready(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    notes_migrations.upgrade(fresh_database_url)
    notes_migrations.downgrade(fresh_database_url)  # alembic_version is left, empty
    await refused(notes_migrations.readiness(), fresh_database_url)


async def test_a_database_behind_this_version_is_not_ready(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    notes_migrations.upgrade(fresh_database_url, "0001")
    await refused(notes_migrations.readiness(), fresh_database_url)


async def test_a_database_at_this_version_s_head_is_ready(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    notes_migrations.upgrade(fresh_database_url)
    await passes(notes_migrations.readiness(), fresh_database_url)


async def test_a_database_a_newer_version_migrated_is_ready_for_this_one(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    """During a rollout the new version migrates first; the old replicas keep serving on the grown schema."""
    notes_migrations.upgrade(fresh_database_url)
    execute(fresh_database_url, "UPDATE alembic_version SET version_num = '0003'")
    await passes(notes_migrations.readiness(), fresh_database_url)


async def test_once_ready_the_check_asks_the_database_no_more(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    readiness = notes_migrations.readiness()
    notes_migrations.upgrade(fresh_database_url)
    await passes(readiness, fresh_database_url)
    notes_migrations.downgrade(fresh_database_url)
    await passes(readiness, fresh_database_url)  # remembered: every probe would otherwise open a connection
    await refused(notes_migrations.readiness(), fresh_database_url)


async def test_the_check_answers_while_every_pooled_connection_is_busy(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    """A pool that requests keep busy is a busy service: readiness reads on a connection of its own."""
    notes_migrations.upgrade(fresh_database_url)
    limits = DatabaseLimits(pool_timeout=timedelta(seconds=30))
    async with (
        Database.open(fresh_database_url, pool_size=1, limits=limits) as database,
        database.transaction(None),  # the pool's one connection
        database.transaction(None),  # and its overflow
        asyncio.timeout(5),
    ):
        await notes_migrations.readiness()(database)


# ── Data migrations under row-level security ────────────────────────────────────


async def test_a_backfill_reaches_every_company_s_rows_through_their_policy(
    notes_migrations: Migrations, fresh_database_url: str
) -> None:
    notes_migrations.upgrade(fresh_database_url, "0001")
    async with Database.open(fresh_database_url, pool_size=1) as database:
        for tenant, body in ((ALPHA, "one"), (ALPHA, "three"), (BETA, "seven"), (ELSEWHERE, "elsewhere")):
            await write(database, tenant, body)
        notes_migrations.upgrade(fresh_database_url)
        assert await lengths(database, ALPHA) == [("one", 3), ("three", 5)]
        assert await lengths(database, BETA) == [("seven", 5)]
        assert await lengths(database, ELSEWHERE) == [("elsewhere", 9)]
    engine = create_engine(fresh_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert forced(connection) == {"notes": True}  # the policy binds the owner again
    finally:
        engine.dispose()


def create(connection: Connection, *statements: str) -> None:
    NOTES.create(connection)
    NOTEBOOKS.create(connection)
    TAGS.create(connection)
    for statement in statements:
        connection.execute(text(statement))


def in_scope(connection: Connection, scope: Scope | None) -> None:
    connection.execute(SET_SCOPE, scope_settings(scope))


def add_note(connection: Connection, tenant: Tenant, body: str, length: int | None = None) -> None:
    in_scope(connection, tenant)
    connection.execute(
        NOTES.insert().values(
            id=new_id(),
            organization_id=tenant.organization,
            company_id=tenant.company,
            body=body,
            length=length,
        )
    )
    in_scope(connection, None)


def test_rows_are_counted_by_tenant_past_the_policy_which_binds_the_owner_again_after(
    owner: Connection,
) -> None:
    create(owner, *tenant_isolation(NOTES))
    for tenant, body in ((ALPHA, "a"), (ALPHA, "b"), (BETA, "c"), (ELSEWHERE, "d")):
        add_note(owner, tenant, body)
    add_note(owner, BETA, "done", length=4)
    missing = NOTES.c.length.is_(None)
    assert rows_by_scope(owner, NOTES, missing) == {ALPHA: 2, BETA: 1, ELSEWHERE: 1}
    assert rows_by_scope(owner, NOTES) == {ALPHA: 2, BETA: 2, ELSEWHERE: 1}
    assert forced(owner) == {"notes": True, "notebooks": False, "tags": False}
    assert owner.execute(NOTES.select()).all() == []  # no scope: the owner sees nothing again


def test_rows_of_a_table_an_organization_owns_are_counted_by_organization(owner: Connection) -> None:
    create(owner, *organization_isolation(NOTEBOOKS))
    other = ELSEWHERE.organization
    for organization in (ORGANIZATION, ORGANIZATION, other):
        in_scope(owner, organization)
        owner.execute(NOTEBOOKS.insert().values(id=new_id(), organization_id=organization))
    in_scope(owner, None)
    assert rows_by_scope(owner, NOTEBOOKS) == {ORGANIZATION: 2, other: 1}
    expected = rows_by_scope(owner, NOTEBOOKS, NOTEBOOKS.c.title.is_(None))
    run_per_scope(owner, update(NOTEBOOKS).values(title="untitled"), expected)
    assert rows_by_scope(owner, NOTEBOOKS, NOTEBOOKS.c.title.is_(None)) == {}


@pytest.mark.parametrize(
    "statements",
    [
        (),
        ("CREATE POLICY open_to_all ON notebooks USING (true)",),
        ("ALTER TABLE notebooks ENABLE ROW LEVEL SECURITY",),
    ],
    ids=["no-security", "a-policy-not-enabled", "enabled-without-an-isolation-policy"],
)
def test_a_table_under_no_isolation_policy_is_refused(owner: Connection, statements: tuple[str, ...]) -> None:
    """Without a policy to say whose a row is, a count by scope could only guess it from the columns."""
    create(owner, *statements)
    with pytest.raises(NoIsolationPolicyError) as caught:
        rows_by_scope(owner, NOTEBOOKS)
    assert caught.value.code == "no_isolation_policy"
    assert str(caught.value) == (
        "table notebooks is under neither tenant_isolation nor organization_isolation: "
        "whose its rows are is unknown"
    )


def test_an_organization_s_table_with_a_company_column_is_counted_by_organization(owner: Connection) -> None:
    """The policy decides the scope, not the columns: an organization's rows that name a company are still the
    organization's, and a statement run per company would miss them."""
    create(owner, *organization_isolation(TAGS))
    for tenant, label in ((ALPHA, "a"), (BETA, "b"), (BETA, "c"), (ELSEWHERE, "d")):
        in_scope(owner, tenant.organization)
        owner.execute(
            TAGS.insert().values(
                id=new_id(), organization_id=tenant.organization, company_id=tenant.company, label=label
            )
        )
    in_scope(owner, None)
    unread = TAGS.c.read.is_(None)
    expected = rows_by_scope(owner, TAGS, unread)
    assert expected == {ORGANIZATION: 3, ELSEWHERE.organization: 1}
    run_per_scope(owner, update(TAGS).where(unread).values(read=True), expected)
    assert rows_by_scope(owner, TAGS, unread) == {}
    assert rows_by_scope(owner, TAGS, TAGS.c.read.is_(True)) == expected


def test_a_policy_not_forced_on_the_owner_is_read_as_it_is_and_left_unforced(owner: Connection) -> None:
    create(owner, *(statement for statement in tenant_isolation(NOTES) if "FORCE" not in statement))
    add_note(owner, ALPHA, "a")
    assert rows_by_scope(owner, NOTES) == {ALPHA: 1}
    assert forced(owner) == {"notes": False, "notebooks": False, "tags": False}


def test_a_count_that_fails_still_puts_the_force_back(owner: Connection) -> None:
    create(owner, *tenant_isolation(NOTES))
    with pytest.raises(ProgrammingError):
        rows_by_scope(owner, NOTES, literal_column("no_such_column").is_(None))
    assert forced(owner) == {"notes": True, "notebooks": False, "tags": False}  # and the transaction goes on


def test_a_statement_the_policy_confines_to_fewer_rows_than_counted_fails(owner: Connection) -> None:
    """The scope an organization's table needs, used on a company's table: the policy hides every row."""
    create(owner, *tenant_isolation(NOTES))
    add_note(owner, ALPHA, "a")
    by_organization: dict[Scope, int] = {ORGANIZATION: 1}
    with pytest.raises(UnexpectedRowCountError) as caught:
        run_per_scope(owner, update(NOTES).values(length=1), by_organization)
    assert caught.value.code == "unexpected_row_count"
    assert (
        str(caught.value)
        == f"the statement changed 0 rows in organization {ORGANIZATION}, not the 1 expected"
    )
    in_alpha = f"changed 1 rows in company {ALPHA.company} of organization {ORGANIZATION}, not the 2"
    with pytest.raises(UnexpectedRowCountError, match=in_alpha):
        run_per_scope(owner, update(NOTES).values(length=1), {ALPHA: 2})


def test_after_the_runs_the_transaction_is_left_without_a_scope(owner: Connection) -> None:
    create(owner, *tenant_isolation(NOTES))
    add_note(owner, ALPHA, "a")
    add_note(owner, BETA, "b")
    run_per_scope(owner, update(NOTES).values(length=1), {ALPHA: 1, BETA: 1})
    assert rows_by_scope(owner, NOTES, NOTES.c.length == 1) == {ALPHA: 1, BETA: 1}
    settings = owner.execute(
        text("SELECT current_setting('ledger.organization_id'), current_setting('ledger.company_id')")
    ).one()
    assert tuple(settings) == ("", "")
