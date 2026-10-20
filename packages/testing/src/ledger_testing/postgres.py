"""One PostgreSQL server per test run, and per-worker databases cloned from one prepared template (§14.2).

Under xdist the controller starts the server and hands its address to each worker (``ledger_testing.plugin``).
The template is built once per server, under an advisory lock, into ``<name>_building`` and renamed when the
hooks (``pytest_ledger_template``) are done, so a failed build never leaves a half template behind. Clones are
serialized by the same lock: ``CREATE DATABASE ... TEMPLATE`` refuses a template another session is using.
Every connection here is the superuser's; tests connect as the roles an app connects as. The platform is
imported lazily: the pytest plugin loads this module before coverage measures anything.
"""

import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Final, final
lazy from collections.abc import Callable, Generator, Iterable, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import args_row
from pydantic import TypeAdapter
from testcontainers.community.postgres import PostgresContainer

from ledger_testing.images import image
lazy from ledger_platform.config.settings import DATABASE_URL_SCHEME

TEMPLATE: Final = "ledger_template"
_LOCK: Final = 0x6C65646774706C00
"""``ledgtpl`` in ASCII: the advisory lock that serializes template builds and clones."""
_PORT: Final = 5432
_NUMBER: Final = TypeAdapter(int)
_TABLES: Final = """
    SELECT schemaname::text, tablename::text FROM pg_tables
    WHERE schemaname = ANY(%s) AND tablename <> 'alembic_version'
      AND (schemaname, tablename) <> ('platform', 'routing')
"""
"""What ``truncate`` empties: Alembic's version tables and the routing version are state, not test data."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresServer:
    """Where the session's server listens, and its superuser."""

    host: str
    port: int
    user: str
    password: str

    def libpq(self, database: str = "postgres") -> str:
        """The superuser's libpq URL of ``database``."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{database}"

    def sqlalchemy(self, database: str, *, role: str, password: str) -> str:
        """The SQLAlchemy URL (async psycopg driver) of ``database`` as ``role``."""
        return f"{DATABASE_URL_SCHEME}{role}:{password}@{self.host}:{self.port}/{database}"

    def database(self, name: str, *, role: str, password: str) -> str:
        """A new database ``name`` owned by a new login role ``role`` (no superuser, no BYPASSRLS): its URL.

        A superuser bypasses row-level security, and so does a table's owner unless the table forces it: tests
        connect as the kind of role an app connects as, never as the administrator.
        """
        with psycopg.connect(self.libpq(), autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role))
            )
        return self.sqlalchemy(name, role=role, password=password)

    def to_json(self) -> str:
        """The address, for an xdist worker's ``workerinput``."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> PostgresServer:
        """The address a controller handed over."""
        return _SERVER.validate_json(text)


_SERVER: Final = TypeAdapter(PostgresServer)


@contextmanager
def started_postgres() -> Generator[PostgresServer]:
    """A PostgreSQL server (the pinned image) for the block, in a container named for the run."""
    container = PostgresContainer(image("postgres"), driver=None).with_name(
        f"ledger-tests-{uuid.uuid7().hex}"
    )
    with container:
        yield PostgresServer(
            host=container.get_container_host_ip(),
            port=_NUMBER.validate_python(container.get_exposed_port(_PORT)),
            user=container.username,
            password=container.password,
        )


@final
class Template:
    """The database being prepared as the template (``pytest_ledger_template`` receives it)."""

    __slots__ = ("_server", "name")

    def __init__(self, server: PostgresServer, name: str) -> None:
        self._server = server
        self.name = name

    @property
    def server(self) -> PostgresServer:
        """The server it lives on (roles are the server's, shared by every database)."""
        return self._server

    def execute(self, statements: Iterable[str], *, database: str | None = None) -> None:
        """Run ``statements`` as the superuser, in the template (or in ``database``), each committed.

        The statements are the tests' own (DDL, grants, rows), never input: they are sent as written.
        """
        with psycopg.connect(self._server.libpq(database or self.name), autocommit=True) as connection:
            for statement in statements:
                connection.execute(statement.encode())

    def url(self, *, role: str, password: str) -> str:
        """The template's SQLAlchemy URL as ``role`` (migrations run as the migrator role)."""
        return self._server.sqlalchemy(self.name, role=role, password=password)


@contextmanager
def server_wide(server: PostgresServer) -> Generator[psycopg.Connection]:
    """A superuser connection holding the lock template builds and clones hold.

    Roles are the server's, shared by every database: a test that creates, alters or drops roles runs its
    statements inside this, so that no worker's template build changes the same catalog rows meanwhile
    (PostgreSQL answers "tuple concurrently updated" to the second writer).
    """
    with psycopg.connect(server.libpq(), autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (_LOCK,))
        try:
            yield admin
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (_LOCK,))


def ensure_template(
    server: PostgresServer, prepare: Callable[[Template], None], *, name: str = TEMPLATE
) -> None:
    """Build the template ``name`` with ``prepare`` unless it exists: once per server, for every worker."""
    building = f"{name}_building"
    with server_wide(server) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None:
            return
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(building)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(building)))
        prepare(Template(server, building))
        admin.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(building), sql.Identifier(name))
        )


def clone(server: PostgresServer, name: str, *, template: str = TEMPLATE) -> None:
    """A fresh database ``name``, a copy of ``template`` (an old one of that name is dropped first)."""
    with server_wide(server) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template))
        )


def _qualified(schema: str, table: str) -> sql.Identifier:
    return sql.Identifier(schema, table)


@final
@dataclass(frozen=True, slots=True)
class WorkerDatabase:
    """This worker's database: a clone of the template, its own for the whole session."""

    server: PostgresServer
    name: str

    def url(self, *, role: str, password: str) -> str:
        """Its SQLAlchemy URL as ``role``."""
        return self.server.sqlalchemy(self.name, role=role, password=password)

    def admin_url(self) -> str:
        """Its superuser libpq URL: for a test's setup and cleanup only."""
        return self.server.libpq(self.name)

    def truncate(self, schemas: Sequence[str]) -> None:
        """Empty every table of ``schemas`` but Alembic's version tables and ``platform.routing``."""
        with psycopg.connect(self.admin_url(), autocommit=True) as connection:
            cursor = connection.cursor(row_factory=args_row(_qualified))
            tables = cursor.execute(_TABLES, (list(schemas),)).fetchall()
            if tables:
                connection.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(tables)))
