"""What a service's migration tests compare: the schema its revisions built, read back from PostgreSQL.

Alembic's comparison (``pending_changes``) sees tables, columns, types, defaults, indexes and foreign keys; it
does not compare check constraints, primary keys, the predicate of a partial index or row-level security,
which the other readers return for a test to compare with the models. Each opens a connection of its own to
the database at ``url`` (an SQLAlchemy ``postgresql+psycopg://`` URL) and closes it, and reads one schema:
``public`` unless ``schema`` names another.
"""

from contextlib import contextmanager
from typing import Final
lazy from collections.abc import Generator, Mapping

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection

_PUBLIC: Final = "public"
"""The schema the readers look at unless told another (a v2 module's tables are in a schema of its own)."""
_NAMED: Final = TypeAdapter(list[tuple[str, str]])
"""Rows of a name and a definition."""
_POLICIES: Final = TypeAdapter(list[tuple[str, str]])
_SECURITY: Final = TypeAdapter(list[tuple[str, bool, bool]])
_CHECKS_SQL: Final = text(
    """
    SELECT conname::text, pg_get_constraintdef(oid) FROM pg_constraint
    WHERE contype = 'c' AND connamespace = CAST(:schema AS regnamespace)
    """
)
_INDEXES_SQL: Final = text(
    "SELECT indexname::text, indexdef FROM pg_indexes WHERE schemaname = :schema AND tablename = :table"
)
_POLICIES_SQL: Final = text(
    "SELECT tablename::text, policyname::text FROM pg_policies WHERE schemaname = :schema ORDER BY 1, 2"
)
_SECURITY_SQL: Final = text(
    """
    SELECT relname::text, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relkind = 'r' AND relnamespace = CAST(:schema AS regnamespace)
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


def tables_of(url: str, *, schema: str = _PUBLIC) -> set[str]:
    """The names of the tables of ``schema``."""
    with _connected(url) as connection:
        return set(inspect(connection).get_table_names(schema=schema))


def pending_changes(url: str, models: MetaData, *, schema: str | None = None) -> str | None:
    """The migration ``models`` would still need, as Alembic would write it; ``None`` when there is none.

    Without ``schema`` Alembic compares the default schema (``public``); with it, that schema alone, whose
    tables ``models`` name with their schema (a v2 schema per module), Alembic's version table aside.
    """

    def in_schema(name: str | None, kind: str, parents: Mapping[str, str | None]) -> bool:
        del parents
        match kind:
            case "schema":
                return name == schema
            case "table":
                return name != "alembic_version"  # Alembic's own, in the schema it migrates
            case _:
                return True

    options: dict[str, object] = {"compare_type": True, "compare_server_default": True}
    if schema is not None:
        options |= {"include_schemas": True, "include_name": in_schema}
    with _connected(url) as connection:
        context = MigrationContext.configure(connection, opts=options)
        operations = produce_migrations(context, models).upgrade_ops
        return None if operations is None or operations.is_empty() else render_python_code(operations)


def check_constraints(url: str, *, schema: str = _PUBLIC) -> dict[str, str]:
    """Every check constraint of ``schema``, by name, as PostgreSQL prints its definition back."""
    with _connected(url) as connection:
        rows = connection.execute(_CHECKS_SQL, {"schema": schema})
        return dict(_NAMED.validate_python([tuple(row) for row in rows]))


def primary_keys(url: str, *, schema: str = _PUBLIC) -> dict[str, list[str]]:
    """The columns of the primary key of every table of ``schema``, in order, by table."""
    with _connected(url) as connection:
        found = inspect(connection)
        return {
            name: list(found.get_pk_constraint(name, schema=schema)["constrained_columns"])
            for name in found.get_table_names(schema=schema)
        }


def indexes_of(url: str, table: str, *, schema: str = _PUBLIC) -> dict[str, str]:
    """Every index of ``table``, by name, as PostgreSQL prints its definition back (predicate included)."""
    with _connected(url) as connection:
        rows = connection.execute(_INDEXES_SQL, {"schema": schema, "table": table})
        return dict(_NAMED.validate_python([tuple(row) for row in rows]))


def policies(url: str, *, schema: str = _PUBLIC) -> list[tuple[str, str]]:
    """Every row-level security policy of ``schema``, as ``(table, policy)``, in order."""
    with _connected(url) as connection:
        rows = connection.execute(_POLICIES_SQL, {"schema": schema})
        return _POLICIES.validate_python([tuple(row) for row in rows])


def row_security(url: str, *, schema: str = _PUBLIC) -> dict[str, tuple[bool, bool]]:
    """Whether each table of ``schema`` has row-level security enabled, and forced on its owner."""
    with _connected(url) as connection:
        rows = connection.execute(_SECURITY_SQL, {"schema": schema})
        security = _SECURITY.validate_python([tuple(row) for row in rows])
    return {name: (enabled, forced) for name, enabled, forced in security}
