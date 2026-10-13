"""Per-schema migrations (§6.5): as the schema's owner, one migrator at a time, readiness from the version."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING, final
lazy from collections.abc import Iterator
lazy from pathlib import Path

import psycopg
import pytest
from alembic import command
from psycopg import sql
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from ledger_platform.data.database import Database
from ledger_platform.data.migrations import (
    PLATFORM_MIGRATIONS,
    MigrationLockTimeoutError,
    MigrationTargetMissingError,
    OfflineMigrationsError,
    SchemaMigrations,
    SchemaNotCurrentError,
)
from ledger_platform.data.roles import APP, MIGRATOR
from ledger_testing.database import TEST_LOGINS, fresh_database

if TYPE_CHECKING:
    from ledger_testing.postgres import PostgresServer, WorkerDatabase

PLATFORM_SCRIPTS = "ledger_platform.data:revisions"
ENV = "from ledger_platform.data.migrations import run_schema_migrations\n\nrun_schema_migrations()\n"
REVISION = """from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1

revision = "{revision}"
down_revision = {down}
branch_labels = None
depends_on = None


def upgrade():
    {chassis}op.execute("{statement}")
"""


@final
class Fresh:
    """A new database with the schemas ``platform`` and ``probe`` and nothing in them."""

    def __init__(self, server: PostgresServer, name: str) -> None:
        self.server = server
        self.name = name

    def url(self, role: str = MIGRATOR, password: str = TEST_LOGINS.migrator) -> str:
        return self.server.sqlalchemy(self.name, role=role, password=password)

    def admin(self) -> psycopg.Connection:
        return psycopg.connect(self.server.libpq(self.name), autocommit=True)

    def one(self, query: sql.SQL | bytes) -> tuple[object, ...]:
        with self.admin() as connection:
            row = connection.execute(query).fetchone()
        assert row is not None
        return tuple(row)


@pytest.fixture
def fresh(chassis: WorkerDatabase, postgres: PostgresServer) -> Iterator[Fresh]:
    """The roles come from the template (``chassis``): a fresh database gets only its schemas."""
    del chassis
    name = fresh_database(postgres, ("platform", "probe"))
    yield Fresh(postgres, name)
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def probe_scripts(tmp_path: Path) -> SchemaMigrations:
    """Two revisions of the test schema ``probe``: the chassis and a table, then a column."""
    versions = tmp_path / "versions"
    versions.mkdir()
    (tmp_path / "env.py").write_text(ENV)
    (versions / "r1.py").write_text(
        REVISION.format(
            revision="r1",
            down=None,
            chassis='chassis_schema_v1(op, "probe")\n    ',
            statement="CREATE TABLE probe.things (n int)",
        )
    )
    (versions / "r2.py").write_text(
        REVISION.format(
            revision="r2", down='"r1"', chassis="", statement="ALTER TABLE probe.things ADD COLUMN m int"
        )
    )
    return SchemaMigrations(schema="probe", scripts=str(tmp_path))


def platform() -> SchemaMigrations:
    """The platform's revisions with readiness not yet passed (``PLATFORM_MIGRATIONS`` remembers it did)."""
    return SchemaMigrations(schema="platform", scripts=PLATFORM_SCRIPTS)


@pytest.fixture
def lock_holder(fresh: Fresh) -> Iterator[psycopg.Connection]:
    """A session that holds the platform schema's migration lock."""
    with fresh.admin() as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (PLATFORM_MIGRATIONS.lock_key,))
        yield connection


def test_the_platform_schema_migrates_to_head_and_a_rerun_does_nothing(fresh: Fresh) -> None:
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    assert PLATFORM_MIGRATIONS.head == "0001"
    assert fresh.one(b"SELECT array_agg(version_num) FROM platform.alembic_version") == (["0001"],)
    assert fresh.one(b"SELECT array_agg(version) FROM platform.routing") == ([0],)


def test_tables_belong_to_the_owner_role(fresh: Fresh) -> None:
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    with fresh.admin() as connection:
        owners = connection.execute(
            "SELECT DISTINCT tableowner::text FROM pg_tables WHERE schemaname = 'platform'"
        ).fetchall()
        tables = connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'platform'").fetchone()
    assert owners == [("platform_owner",)]
    assert tables == (9,)


def test_each_schema_has_its_own_version_table_and_lock(
    fresh: Fresh, probe_scripts: SchemaMigrations
) -> None:
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    probe_scripts.upgrade(fresh.url())
    assert fresh.one(b"SELECT version_num FROM probe.alembic_version") == ("r2",)
    assert fresh.one(b"SELECT tableowner::text FROM pg_tables WHERE tablename = 'things'") == ("probe_owner",)
    assert fresh.one(b"SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == (0,)
    assert probe_scripts.lock_key != PLATFORM_MIGRATIONS.lock_key


def test_a_migrator_leaves_the_lock_free_when_it_is_done(fresh: Fresh) -> None:
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    with fresh.admin() as connection:
        taken = connection.execute(
            "SELECT pg_try_advisory_lock(%s)", (PLATFORM_MIGRATIONS.lock_key,)
        ).fetchone()
    assert taken == (True,)


def test_a_second_migrator_waits_for_the_first_then_gives_up(
    fresh: Fresh, lock_holder: psycopg.Connection
) -> None:
    del lock_holder
    started = time.monotonic()
    with pytest.raises(MigrationLockTimeoutError, match="schema platform") as raised:
        PLATFORM_MIGRATIONS.upgrade(fresh.url(), lock_wait=timedelta(seconds=1))
    assert time.monotonic() - started >= 1
    assert raised.value.code == "migration_lock_timeout"
    assert "0:00:01" in str(raised.value)
    assert fresh.one(b"SELECT to_regclass('platform.alembic_version') IS NULL") == (True,)


def test_a_second_migrator_waits_for_the_first_then_finds_head(
    fresh: Fresh, lock_holder: psycopg.Connection
) -> None:
    released = threading.Timer(0.5, lambda: lock_holder.execute("SELECT pg_advisory_unlock_all()"))
    released.start()
    try:
        PLATFORM_MIGRATIONS.upgrade(fresh.url(), lock_wait=timedelta(seconds=30))
    finally:
        released.join()
    assert fresh.one(b"SELECT version_num FROM platform.alembic_version") == ("0001",)


def test_two_migrators_at_once_migrate_once(fresh: Fresh) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [pool.submit(PLATFORM_MIGRATIONS.upgrade, fresh.url()) for _ in range(2)]
        for run in runs:
            run.result()
    assert fresh.one(b"SELECT array_agg(version_num) FROM platform.alembic_version") == (["0001"],)
    assert fresh.one(b"SELECT array_agg(version) FROM platform.routing") == ([0],)


def test_a_migrator_cancelled_for_another_reason_is_not_a_lock_timeout(
    fresh: Fresh, lock_holder: psycopg.Connection
) -> None:
    del lock_holder
    impatient = f"{fresh.url()}?options=-c%20statement_timeout%3D200"
    with pytest.raises(OperationalError) as raised:
        PLATFORM_MIGRATIONS.upgrade(impatient, lock_wait=timedelta(minutes=1))
    assert isinstance(raised.value.orig, psycopg.errors.QueryCanceled)
    assert not isinstance(raised.value, MigrationLockTimeoutError)


async def test_readiness_follows_the_version_table(fresh: Fresh, probe_scripts: SchemaMigrations) -> None:
    async with Database.open(fresh.url(APP, TEST_LOGINS.app), pool_size=1) as database:
        with pytest.raises(SchemaNotCurrentError, match="schema probe is not at revision r2") as raised:
            await probe_scripts.readiness(database)  # never migrated: no version table
        assert raised.value.code == "schema_not_current"
        probe_scripts.upgrade(fresh.url())
        with fresh.admin() as connection:
            connection.execute("UPDATE probe.alembic_version SET version_num = 'r1'")
        with pytest.raises(SchemaNotCurrentError):
            await probe_scripts.readiness(database)  # behind
        with fresh.admin() as connection:
            connection.execute("DELETE FROM probe.alembic_version")
        with pytest.raises(SchemaNotCurrentError):
            await probe_scripts.readiness(database)  # at the base
        with fresh.admin() as connection:
            connection.execute("INSERT INTO probe.alembic_version VALUES ('ffff')")
        await probe_scripts.readiness(database)  # a newer version migrated it
        with fresh.admin() as connection:
            connection.execute("UPDATE probe.alembic_version SET version_num = 'r2'")
        await platform_ready_at_head(fresh, database)


async def platform_ready_at_head(fresh: Fresh, database: Database) -> None:
    migrations = platform()
    PLATFORM_MIGRATIONS.upgrade(fresh.url())
    await migrations.readiness(database)
    nowhere = create_async_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:9/none")
    try:
        await migrations.readiness(Database(nowhere, probe=nowhere))  # passed once: it never asks again
    finally:
        await nowhere.dispose()


def test_offline_mode_is_refused() -> None:
    with pytest.raises(OfflineMigrationsError):
        command.upgrade(PLATFORM_MIGRATIONS.config(), "head", sql=True)


def test_alembic_run_without_a_target_is_refused() -> None:
    with pytest.raises(MigrationTargetMissingError, match="SchemaMigrations"):
        command.upgrade(PLATFORM_MIGRATIONS.config(), "head")


def test_the_lock_key_is_derived_from_the_schema_alone() -> None:
    assert platform().lock_key == PLATFORM_MIGRATIONS.lock_key
    assert -(2**63) <= PLATFORM_MIGRATIONS.lock_key < 2**63
    assert PLATFORM_MIGRATIONS.schema == "platform"
    assert PLATFORM_MIGRATIONS.metadata is None


def test_a_database_is_closed_to_public(fresh: Fresh, probe_scripts: SchemaMigrations) -> None:
    """On a fresh database: ``CREATE DATABASE ... TEMPLATE`` does not copy database-level ACLs, so the
    per-worker clones keep PostgreSQL's defaults for ``PUBLIC`` (``CONNECT``, ``TEMPORARY``); only a database
    ``database_statements`` ran in (the template, a fresh one, production's) is closed."""
    probe_scripts.upgrade(fresh.url())
    outsider = f"outsider_{fresh.name}"
    with fresh.admin() as admin:
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'outsider'").format(sql.Identifier(outsider)))
        admin.execute(
            """SET ROLE probe_owner;
               CREATE FUNCTION probe.answer() RETURNS int LANGUAGE sql AS 'SELECT 42';
               RESET ROLE"""
        )
    try:
        with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
            psycopg.connect(
                fresh.server.libpq(fresh.name).replace(
                    f"{fresh.server.user}:{fresh.server.password}", f"{outsider}:outsider"
                )
            )
        app = fresh.server.libpq(fresh.name).replace(
            f"{fresh.server.user}:{fresh.server.password}", f"{APP}:{TEST_LOGINS.app}"
        )
        with psycopg.connect(app, autocommit=True) as connection:
            for statement in (
                b"CREATE TEMPORARY TABLE scratch (n int)",
                b"CREATE TABLE public.scratch (n int)",
                b"SELECT probe.answer()",
            ):
                for role in (b"RESET ROLE", b"SET ROLE probe_rw"):
                    connection.execute(role)
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        connection.execute(statement)
    finally:
        with fresh.admin() as admin:
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(outsider)))
