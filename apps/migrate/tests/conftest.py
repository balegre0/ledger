"""``ledger-migrate``'s tests: databases of their own, on a server whose roles the template made."""

from typing import Final
lazy from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from ledger_platform.data.roles import APP, MIGRATOR
from ledger_testing.database import TEST_LOGINS, bootstrap, fresh_database
lazy from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase

SCHEMAS: Final = ("platform", "identity")
"""The schemas of every package installed here (the platform's and identity's)."""


def pytest_ledger_template(template: Template) -> None:
    """The roles of the installed packages' schemas (``ledger-migrate`` makes everything else)."""
    bootstrap(template, SCHEMAS)


@pytest.fixture
def fresh(worker_database: WorkerDatabase, postgres: PostgresServer) -> Iterator[str]:
    """A new database with the schemas, empty, as a first rollout finds it; dropped after the test."""
    del worker_database  # its template made the roles the schemas are owned by
    name = fresh_database(postgres, SCHEMAS)
    yield name
    with psycopg.connect(postgres.libpq(), autocommit=True) as connection:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def environment(fresh: str, postgres: PostgresServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """``LEDGER_*`` as a rollout gives ``ledger-migrate``: units as ``ledger_app``, the migrations direct."""
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "test")
    monkeypatch.setenv("LEDGER_DATABASE_URL", postgres.sqlalchemy(fresh, role=APP, password=TEST_LOGINS.app))
    monkeypatch.setenv(
        "LEDGER_DATABASE_DIRECT_URL", postgres.sqlalchemy(fresh, role=MIGRATOR, password=TEST_LOGINS.migrator)
    )
