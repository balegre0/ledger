"""Preparing test databases the way production's are: roles, schemas, migrations (§6.2, §14.2).

The template's hook (``pytest_ledger_template``) calls ``bootstrap`` with every schema the package's tests
use, then ``migrate`` with its migrations; tests connect as ``ledger_app`` (``app_url``) and enter a schema's
``rw`` role per unit, as the apps do. Roles are the server's: every database on it shares them.
"""

import secrets
from typing import Final
lazy from collections.abc import Sequence

import psycopg
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from ledger_platform.data.chassis_schema import chassis_schema_v1
from ledger_platform.data.roles import (
    APP,
    MIGRATOR,
    Logins,
    SchemaRoles,
    cluster_statements,
    database_statements,
    identifier,
    relay_statements,
)
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase

TEST_LOGINS: Final = Logins(app="app-password", migrator="migrator-password", signer="signer-password")
"""The logins' passwords on test servers (``init.sql`` takes the local stack's from its environment)."""


def bootstrap(template: Template, schemas: Sequence[str]) -> None:
    """The cluster's roles and the template's schemas, as ``init.sql`` makes them."""
    roles = tuple(SchemaRoles(schema=schema) for schema in schemas)
    template.execute(cluster_statements(roles, TEST_LOGINS), database="postgres")
    template.execute(database_statements(roles))


def migrate(template: Template, *migrations: SchemaMigrations) -> None:
    """Each schema's revisions as ``ledger_migrator``, as ``ledger-migrate`` does (grants included)."""
    url = template.url(role=MIGRATOR, password=TEST_LOGINS.migrator)
    for migration in migrations:
        migration.upgrade(url)


def create_chassis(template: Template, schema: str) -> None:
    """``chassis_schema_v1`` and the relay's grants in a test schema, as its owner (for platform tests)."""
    owner = SchemaRoles(schema=schema).owner
    engine = create_engine(template.url(role=MIGRATOR, password=TEST_LOGINS.migrator), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL ROLE {identifier(owner)}"))
            chassis_schema_v1(Operations(MigrationContext.configure(connection)), schema)
            for statement in relay_statements(schema):
                connection.execute(text(statement))
    finally:
        engine.dispose()


def fresh_database(server: PostgresServer, schemas: Sequence[str]) -> str:
    """A new, empty database with ``schemas`` (their roles made already, by a template's ``bootstrap``)."""
    name = f"fresh_{secrets.token_hex(6)}"
    with psycopg.connect(server.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(server.libpq(name), autocommit=True) as connection:
        for statement in database_statements(tuple(SchemaRoles(schema=schema) for schema in schemas)):
            connection.execute(statement.encode())
    return name


def app_url(worker: WorkerDatabase) -> str:
    """The worker's database as ``ledger_app`` (SQLAlchemy URL)."""
    return worker.url(role=APP, password=TEST_LOGINS.app)


def migrator_url(worker: WorkerDatabase) -> str:
    """The worker's database as ``ledger_migrator`` (SQLAlchemy URL)."""
    return worker.url(role=MIGRATOR, password=TEST_LOGINS.migrator)


def libpq_as(worker: WorkerDatabase, *, role: str, password: str) -> str:
    """The worker's database as ``role`` (libpq URL, for plain psycopg connections in tests)."""
    server = worker.server
    return f"postgresql://{role}:{password}@{server.host}:{server.port}/{worker.name}"
