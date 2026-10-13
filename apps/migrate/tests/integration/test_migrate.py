import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter

from ledger_app_migrate import main
from ledger_identity.adapters.postgres.schema import MIGRATIONS
from ledger_platform.data.migrations import PLATFORM_MIGRATIONS
from ledger_platform.runtime.app import DirectUrlMissingError
lazy from ledger_testing.postgres import PostgresServer

_VERSION = TypeAdapter(tuple[str])


def version(postgres: PostgresServer, database: str, schema: str) -> str:
    with psycopg.connect(postgres.libpq(database)) as connection:
        row = connection.execute(
            sql.SQL("SELECT version_num FROM {}").format(sql.Identifier(schema, "alembic_version"))
        ).fetchone()
    return _VERSION.validate_python(row)[0]


@pytest.mark.usefixtures("environment")
def test_migrate_brings_every_installed_schema_to_head(fresh: str, postgres: PostgresServer) -> None:
    assert main.main() == 0
    assert version(postgres, fresh, "platform") == PLATFORM_MIGRATIONS.head
    assert version(postgres, fresh, "identity") == MIGRATIONS.head


@pytest.mark.usefixtures("environment")
def test_migrate_twice_finds_head_and_changes_nothing(fresh: str, postgres: PostgresServer) -> None:
    assert (main.main(), main.main()) == (0, 0)
    assert version(postgres, fresh, "identity") == MIGRATIONS.head


@pytest.mark.usefixtures("environment")
def test_migrate_without_a_direct_url_fails_and_the_rollout_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_DIRECT_URL")
    with pytest.raises(DirectUrlMissingError):
        main.main()
