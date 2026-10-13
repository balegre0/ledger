"""Each schema's Alembic revisions, run under its owner role (§6.5, ADR 0004).

``SchemaMigrations`` names one schema's revisions (a package resource,
``ledger_identity.adapters.postgres:migrations``). ``upgrade(url)`` connects as ``ledger_migrator`` directly
to PostgreSQL (never through a pooler: it holds a session lock), takes the schema's advisory lock (waiting at
most ``MIGRATION_LOCK_WAIT``), becomes ``<schema>_owner`` and runs every pending revision, each in its own
transaction, with Alembic's version table inside the schema. A second migrator waits for the first, then
finds head and does nothing; every other lock is waited for at most ten seconds.

``readiness`` is the running app's check: ready when the schema is at this version's head or at a revision
this version does not know (a newer version migrated it during a rollout: the schema only grows); a schema
without a version table was never migrated. A revision package's ``env.py`` is one call:
``run_schema_migrations()``.
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar, Final, final

import psycopg
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import TypeAdapter
from sqlalchemy import String, column, create_engine, select, table, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection, MetaData

from ledger_kernel.errors import LedgerError, UnavailableError
from ledger_platform.data.roles import PLATFORM, SchemaRoles, identifier, migrated_statements
lazy from ledger_platform.data.database import Database

MIGRATION_LOCK_WAIT: Final = timedelta(minutes=10)
"""How long a migrator waits for another one of the same schema: far longer than a migration takes, finite."""
_OTHER_LOCKS: Final = "10s"
"""What every other lock of a migration is waited for: it fails rather than queue the app behind it."""
_TARGET: Final = "ledger.target"
"""The key of the ``_Target`` among the attributes of Alembic's configuration."""
_MILLISECOND: Final = timedelta(milliseconds=1)
_SET_LOCK_TIMEOUT: Final = text("SELECT set_config('lock_timeout', :wait, false)")
_LOCK: Final = text("SELECT pg_advisory_lock(:key)")
_RECORDED: Final = text("SELECT to_regclass(:table) IS NOT NULL")
_FLAG: Final = TypeAdapter(bool)
_VERSION: Final = TypeAdapter(str | None)


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text: they run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self) -> None:
        super().__init__("migrations run against a database, not as SQL text")


class MigrationTargetMissingError(LedgerError):
    """Alembic was run directly, not through ``SchemaMigrations``: it knows no schema or connection."""

    code: ClassVar[str] = "migration_target_missing"

    def __init__(self) -> None:
        super().__init__("the migrations were started without a target: run them through SchemaMigrations")


class MigrationLockTimeoutError(UnavailableError):
    """Another migrator held the schema's lock for longer than this one waits."""

    code: ClassVar[str] = "migration_lock_timeout"

    def __init__(self, schema: str, waited: timedelta) -> None:
        super().__init__(f"another migration of schema {schema} held its lock for longer than {waited}")


class SchemaNotCurrentError(UnavailableError):
    """The schema is behind this version's head, or was never migrated."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, schema: str, head: str) -> None:
        super().__init__(f"schema {schema} is not at revision {head}, the one this version needs")


@final
@dataclass(frozen=True, slots=True)
class _Target:
    """What ``SchemaMigrations.upgrade`` hands ``env.py``: the schema, and the connection holding its lock."""

    schema: str
    connection: Connection
    metadata: MetaData | None


def run_schema_migrations() -> None:
    """The whole of a revision package's ``env.py``: run the pending revisions on the target's connection."""
    if context.is_offline_mode():
        raise OfflineMigrationsError
    target = context.config.attributes.get(_TARGET)
    if not isinstance(target, _Target):
        raise MigrationTargetMissingError
    context.configure(
        connection=target.connection,
        target_metadata=target.metadata,
        version_table_schema=target.schema,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _milliseconds(wait: timedelta) -> str:
    """``lock_timeout``'s text for ``wait``, rounded up: never zero, which PostgreSQL reads as no limit."""
    return f"{max(1, math.ceil(wait / _MILLISECOND))}ms"


@final
class SchemaMigrations:
    """One schema's revisions (see the module)."""

    __slots__ = ("_ready", "_scripts", "metadata", "schema")

    def __init__(self, *, schema: str, scripts: str, metadata: MetaData | None = None) -> None:
        self.schema: Final = SchemaRoles(schema=schema).schema
        self.metadata: Final = metadata
        self._scripts = scripts
        self._ready = False

    def config(self) -> Config:
        """Alembic's configuration of the scripts, with no target: for Alembic's own commands in tests."""
        config = Config()
        config.set_main_option("script_location", self._scripts)
        return config

    def _revisions(self) -> ScriptDirectory:
        return ScriptDirectory.from_config(self.config())

    @property
    def head(self) -> str:
        """The revision this version's scripts end at (one line of history, one head)."""
        (head,) = self._revisions().get_heads()
        return head

    @property
    def lock_key(self) -> int:
        """The schema's advisory lock: a signed 64-bit number derived from its name (stable over versions)."""
        digest = hashlib.sha256(f"ledger.migrate.{self.schema}".encode()).digest()
        return int.from_bytes(digest[:8], "big", signed=True)

    def upgrade(self, url: str, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None:
        """Bring the schema at ``url`` (as ``ledger_migrator``, direct) to head (see the module).

        Raises ``MigrationLockTimeoutError`` when another migrator holds the schema for longer than
        ``lock_wait``.
        """
        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                connection.execute(_SET_LOCK_TIMEOUT, {"wait": _milliseconds(lock_wait)})
                try:
                    connection.execute(_LOCK, {"key": self.lock_key})
                except OperationalError as error:
                    if isinstance(error.orig, psycopg.errors.LockNotAvailable):
                        raise MigrationLockTimeoutError(self.schema, lock_wait) from error
                    raise
                connection.execute(_SET_LOCK_TIMEOUT, {"wait": _OTHER_LOCKS})
                connection.execute(text(f"SET ROLE {identifier(SchemaRoles(schema=self.schema).owner)}"))
                connection.commit()  # the session keeps the lock and the role; each revision commits alone
                config = self.config()
                config.attributes[_TARGET] = _Target(self.schema, connection, self.metadata)
                command.upgrade(config, "head")
                for statement in migrated_statements(self.schema):
                    connection.execute(text(statement))
                connection.commit()
        finally:
            engine.dispose()  # closing the session releases the advisory lock and the role

    async def readiness(self, database: Database) -> None:
        """Raise ``SchemaNotCurrentError`` until the schema is at head or past it; then never ask again."""
        if self._ready:
            return
        known = {revision.revision for revision in self._revisions().walk_revisions()}
        head = self.head
        async with database.probing() as connection:
            await connection.execute(text(f"SET LOCAL ROLE {identifier(SchemaRoles(schema=self.schema).rw)}"))
            found = await connection.execute(_RECORDED, {"table": f"{self.schema}.alembic_version"})
            recorded = _FLAG.validate_python(found.scalar_one())
            versions = table("alembic_version", column("version_num", String), schema=self.schema)
            version = (
                _VERSION.validate_python((await connection.execute(select(versions.c.version_num))).scalar())
                if recorded
                else None
            )
        if version is None or (version in known and version != head):
            raise SchemaNotCurrentError(self.schema, head)
        self._ready = True


PLATFORM_MIGRATIONS: Final = SchemaMigrations(schema=PLATFORM, scripts="ledger_platform.data:revisions")
"""The platform schema's revisions, run first by ``ledger-migrate``."""
