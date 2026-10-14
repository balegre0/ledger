"""The module fixtures (``ledger_testing.modules``), exercised with the platform as the module under test."""

from http import HTTPStatus

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
lazy import httpx

from ledger_platform._manifest import MANIFEST
from ledger_platform.config.manifest import load_manifest
from ledger_platform.data.migrations import SchemaMigrations, platform_migrations
from ledger_platform.data.notifier import OUTBOX
from ledger_testing.modules import ModuleUnderTest, QuietSignals, SchemaLessModuleError
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.app import App
lazy from ledger_testing.postgres import WorkerDatabase

_COUNT = TypeAdapter(int)
_NAMES = TypeAdapter(list[tuple[str]])
KERNEL = '[package]\nname = "kernel"\nkind = "package"\nsummary = "Types."\nowners = ["@o"]\n'


def test_a_module_under_test_owns_a_schema() -> None:
    with pytest.raises(SchemaLessModuleError, match="'kernel' owns no schema"):
        ModuleUnderTest(load_manifest(KERNEL), platform_migrations())


def test_a_module_s_schemas_are_the_platform_s_then_its_own() -> None:
    platform = ModuleUnderTest(MANIFEST, platform_migrations())
    assert platform.schemas == ("platform",)
    books = ModuleUnderTest(MANIFEST, SchemaMigrations(schema="books", scripts="books_unused:migrations"))
    assert (books.schema, books.schemas) == ("books", ("platform", "books"))
    assert platform.publishes == frozenset()


def test_quiet_signals_tell_no_one() -> None:
    QuietSignals().signal(OUTBOX)


async def test_the_module_s_units_run_in_its_role_and_its_rows_are_emptied_after(
    module_database: BoundDatabase, migrated: WorkerDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with module_database.unit(None) as unit:
        role = await unit.connection.execute(text("SELECT current_user"))
        assert role.scalar_one() == "platform_rw"
        await unit.connection.execute(
            text("INSERT INTO platform.schedule_leases (name, next_run_at) VALUES ('probe.tick', now())")
        )
    rows = await admin.execute("SELECT count(*) FROM platform.schedule_leases")
    assert _COUNT.validate_python((await rows.fetchone() or (0,))[0]) == 1
    assert migrated.name.startswith("ledger_")


def test_the_settings_point_at_the_worker_s_database_and_the_test_s_provider(
    settings: Settings, migrated: WorkerDatabase
) -> None:
    assert migrated.name in settings.database_url.get_secret_value()
    assert settings.database_direct_url is not None
    assert settings.token_issuer is not None


async def test_the_client_talks_to_the_started_api_app(api: App, client: httpx.AsyncClient) -> None:
    assert api.modules  # every installed module, found by its entry point
    assert (await client.get("/livez")).status_code == HTTPStatus.OK


def test_a_fresh_database_holds_the_module_s_schemas_empty(
    fresh_database_name: str, fresh: Settings, fresh_admin_url: str
) -> None:
    assert fresh_database_name in fresh.database_url.get_secret_value()
    with psycopg.connect(fresh_admin_url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        found = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname = 'platform'").fetchall()
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'platform'"
        ).fetchall()
    assert _NAMES.validate_python(found) == [("platform",)]
    assert _NAMES.validate_python(tables) == []
