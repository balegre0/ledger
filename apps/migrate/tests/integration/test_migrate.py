"""``ledger-migrate`` on fresh databases: every installed schema, named by the manifests, ends at its head.

Modules' and channels' alike: a country's channel owns a schema of its own (do-ecf-v1, ``do_ecf``).
"""

import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter

from ledger_app_migrate import main
from ledger_platform._manifest import MANIFEST as PLATFORM
from ledger_platform.config.settings import Settings
from ledger_platform.runtime.app import App, DirectUrlMissingError
from ledger_platform.runtime.packs import Channels, load_modules
from ledger_platform.runtime.registries import Regimes
lazy from ledger_testing.postgres import PostgresServer

_VERSION = TypeAdapter(tuple[str])


def version(postgres: PostgresServer, database: str, schema: str) -> str:
    with psycopg.connect(postgres.libpq(database)) as connection:
        row = connection.execute(
            sql.SQL("SELECT version_num FROM {}").format(sql.Identifier(schema, "alembic_version"))
        ).fetchone()
    return _VERSION.validate_python(row)[0]


def heads() -> dict[str, str]:
    """Each installed schema's head, as the app that migrates assembles them (no I/O)."""
    return {
        migrations.schema: migrations.head for migrations in App("migrate", settings=Settings()).migrations
    }


@pytest.mark.usefixtures("environment")
def test_migrate_brings_every_installed_schema_to_head(fresh: str, postgres: PostgresServer) -> None:
    expected = heads()
    channels = Channels.load(Regimes.load()).packs
    named = {
        PLATFORM.schema_name,
        *(module.manifest.schema_name for module in load_modules()),
        *(pack.entry.schema_name for pack in channels),
    }
    assert set(expected) == named  # every schema a manifest names, none more
    assert main.main() == 0
    assert {schema: version(postgres, fresh, schema) for schema in expected} == expected


@pytest.mark.usefixtures("environment")
def test_migrate_twice_finds_head_and_changes_nothing(fresh: str, postgres: PostgresServer) -> None:
    assert (main.main(), main.main()) == (0, 0)
    assert {schema: version(postgres, fresh, schema) for schema in heads()} == heads()


@pytest.mark.usefixtures("environment")
def test_migrate_without_a_direct_url_fails_and_the_rollout_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_DIRECT_URL")
    with pytest.raises(DirectUrlMissingError):
        main.main()
