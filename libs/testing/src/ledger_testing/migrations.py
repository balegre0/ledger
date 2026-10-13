"""What a service's migration tests compare: the schema its revisions built, read back from PostgreSQL.

Alembic's comparison (``pending_changes``) sees tables, columns, types, defaults, indexes and foreign keys; it
does not compare check constraints, primary keys, the predicate of a partial index or row-level security,
which the other readers return for a test to compare with the models. Each opens a connection of its own to
the database at ``url`` (an SQLAlchemy ``postgresql+psycopg://`` URL) and closes it.
"""

from contextlib import contextmanager
from typing import Final
lazy from collections.abc import Generator

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection

_NAMED: Final = TypeAdapter(list[tuple[str, str]])
"""Rows of a name and a definition."""
_POLICIES: Final = TypeAdapter(list[tuple[str, str]])
_SECURITY: Final = TypeAdapter(list[tuple[str, bool, bool]])
_CHECKS_SQL: Final = text(
    """
    SELECT conname::text, pg_get_constraintdef(oid) FROM pg_constraint
    WHERE contype = 'c' AND connamespace = 'public'::regnamespace
    """
)
_INDEXES_SQL: Final = text(
    "SELECT indexname::text, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = :table"
)
_POLICIES_SQL: Final = text("SELECT tablename::text, policyname::text FROM pg_policies ORDER BY 1, 2")
_SECURITY_SQL: Final = text(
    """
    SELECT relname::text, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
    """
)


@contextmanager
def _connected(url: str) -> Generator[Connection]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def combined(*metadata: MetaData) -> MetaData:
    """The tables of every ``metadata`` in one, as a service's migrations must make them all."""
    together = MetaData()
    for each in metadata:
        for table in each.tables.values():
            table.to_metadata(together)
    return together


def tables_of(url: str) -> set[str]:
    """The names of the database's tables."""
    with _connected(url) as connection:
        return set(inspect(connection).get_table_names())


def pending_changes(url: str, models: MetaData) -> str | None:
    """The migration ``models`` would still need, as Alembic would write it; ``None`` when there is none."""
    with _connected(url) as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        operations = produce_migrations(context, models).upgrade_ops
        return None if operations is None or operations.is_empty() else render_python_code(operations)


def check_constraints(url: str) -> dict[str, str]:
    """Every check constraint of the database, by name, as PostgreSQL prints its definition back."""
    with _connected(url) as connection:
        return dict(_NAMED.validate_python([tuple(row) for row in connection.execute(_CHECKS_SQL)]))


def primary_keys(url: str) -> dict[str, list[str]]:
    """The columns of every table's primary key, in order, by table."""
    with _connected(url) as connection:
        schema = inspect(connection)
        return {
            name: list(schema.get_pk_constraint(name)["constrained_columns"])
            for name in schema.get_table_names()
        }


def indexes_of(url: str, table: str) -> dict[str, str]:
    """Every index of ``table``, by name, as PostgreSQL prints its definition back (predicate included)."""
    with _connected(url) as connection:
        rows = connection.execute(_INDEXES_SQL, {"table": table})
        return dict(_NAMED.validate_python([tuple(row) for row in rows]))


def policies(url: str) -> list[tuple[str, str]]:
    """Every row-level security policy, as ``(table, policy)``, in order."""
    with _connected(url) as connection:
        return _POLICIES.validate_python([tuple(row) for row in connection.execute(_POLICIES_SQL)])


def row_security(url: str) -> dict[str, tuple[bool, bool]]:
    """Whether each table has row-level security enabled, and forced on its owner."""
    with _connected(url) as connection:
        rows = connection.execute(_SECURITY_SQL)
        security = _SECURITY.validate_python([tuple(row) for row in rows])
    return {name: (enabled, forced) for name, enabled, forced in security}
