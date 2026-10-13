"""Every service's schema brought to its version's (Alembic): the runner, readiness, data migrations.

A service keeps its revisions in its package (``<package>.adapters.postgres:migrations``), names them with a
``Migrations``, and its ``env.py`` is one call, ``run_migrations(<the service's metadata>)``. Migrations run
on a synchronous psycopg engine, so the same code runs in a test, in the one-shot container that migrates
before the service starts, and on a laptop; never as SQL text. Each upgrade or downgrade is one transaction
that first takes a transaction-level advisory lock (``MIGRATION_LOCK``, per database), so a replica that
starts while another migrates waits for it, then finds the database at head and does nothing. That wait is
long but finite (``MIGRATION_LOCK_WAIT``); every other lock is waited for at most ten seconds: a migration
that cannot get one fails, it never queues the service's own statements behind it.

The running service's side is ``SchemaReadiness`` (``Migrations.readiness()``): ready when the database is at
this version's head, or at a revision this version's scripts do not know (a newer version migrated it during a
rollout: the schema only grows, so an older version keeps working on it); not ready when it is behind, or was
never migrated. It reads on a connection of its own, never the pool's, and stops asking once it has passed.

Data migrations. Every tenant table is under row-level security forced on its owner, so a statement a
migration runs with no scope touches nothing, and says so only by its row count. ``rows_by_scope`` counts
the rows a backfill must change, per scope, past the policies; ``run_per_scope`` runs the statement once in
each scope and fails unless each run changed exactly that many: a policy that hides a row, or a scope that
was not set, is an error instead of a silent no-op.
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping

import psycopg
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String, Uuid, column, create_engine, func, select, table, text, true, type_coerce
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import ColumnElement, Connection, Delete, MetaData, Update

from ledger_kernel.errors import LedgerError, UnavailableError
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant
from ledger_platform.database import SET_SCOPE, ddl_name, scope_settings
from ledger_platform.options import InvalidOptionError
from ledger_platform.schema import CHASSIS_METADATA
lazy from ledger_platform.database import AnyTable, Database, Scope

HEAD: Final = "head"
BASE: Final = "base"
MIGRATION_LOCK: Final = 0x6C65646765720001
"""The advisory lock a migration holds until it commits: ``ledger`` in ASCII, then the lock's number.

Advisory locks are per database, and every service's database is its own."""
MIGRATION_LOCK_WAIT: Final = timedelta(minutes=10)
"""How long a migration waits for another one to commit: far longer than any migration takes, so that a
replica starting while another migrates waits for it; yet finite, so that a migration stuck (or a session that
took the lock and never let go) fails the next one loudly instead of hanging it forever."""
LONGEST_LOCK_WAIT: Final = timedelta(milliseconds=2**31 - 1)
"""The longest ``lock_timeout`` PostgreSQL accepts."""
_LOCK_TIMEOUT: Final = "10s"
"""What every other lock of a migration is waited for."""
_MILLISECOND: Final = timedelta(milliseconds=1)
_TARGET: Final = "ledger.target"
"""The key of the ``Target`` among the attributes of Alembic's configuration."""
_RECORDED: Final = select(func.to_regclass("public.alembic_version").is_not(None))
"""Whether Alembic's table exists: a database no migration ever ran on has none."""
_VERSION: Final = select(column("version_num", String)).select_from(table("alembic_version", schema="public"))
"""The revisions Alembic recorded: one row at a revision, none at the base."""
_FORCED: Final = text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:table)")


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text. They run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self) -> None:
        super().__init__("migrations run against a database, not as SQL text")


class MigrationUrlMissingError(LedgerError):
    """The migrations were started with no target: Alembic's configuration holds no ``Target``.

    Alembic was run directly instead of through ``Migrations``.
    """

    code: ClassVar[str] = "migration_url_missing"

    def __init__(self) -> None:
        super().__init__("the migrations were started without a database URL: run them through Migrations")


class InvalidMigrationOptionError(InvalidOptionError):
    """A migration was asked to run with an option that cannot work."""

    code: ClassVar[str] = "invalid_migration_option"
    label: ClassVar[str] = "migration option"


class MigrationLockTimeoutError(UnavailableError):
    """Another migration held the migration lock for longer than this one would wait."""

    code: ClassVar[str] = "migration_lock_timeout"

    def __init__(self, owner: str, waited: timedelta) -> None:
        super().__init__(f"another migration of {owner}'s database held its lock for longer than {waited}")


class SchemaNotCurrentError(UnavailableError):
    """The database is behind the revision this version needs, or was never migrated."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, expected: str) -> None:
        super().__init__(f"the database schema is not at revision {expected}, the one this version needs")


class UnexpectedRowCountError(LedgerError):
    """A data migration's statement changed another number of rows in a scope than it had to.

    A defect of the migration (a policy hides rows from the scope, the statement filters differently from the
    count): the migration's transaction rolls back, and nothing of it is kept.
    """

    code: ClassVar[str] = "unexpected_row_count"

    def __init__(self, *, scope: Scope, expected: int, changed: int) -> None:
        super().__init__(
            f"the statement changed {changed} rows in {_named(scope)}, not the {expected} expected"
        )


def _named(scope: Scope) -> str:
    match scope:
        case Tenant(organization=organization, company=company):
            return f"company {company} of organization {organization}"
        case organization:
            return f"organization {organization}"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    """What the migration environment runs against: the database, whose it is, and how long to wait."""

    url: str = field(repr=False)  # it carries the password
    owner: str
    """The service the migrations are of, for the messages of their errors."""
    lock_wait: timedelta
    """Positive, and at most ``LONGEST_LOCK_WAIT``: a zero ``lock_timeout`` would be no bound at all."""

    def __post_init__(self) -> None:
        if not timedelta(0) < self.lock_wait <= LONGEST_LOCK_WAIT:
            raise InvalidMigrationOptionError(
                option="lock_wait",
                requirement=f"between 1 microsecond and {LONGEST_LOCK_WAIT // _MILLISECOND} milliseconds",
                value=self.lock_wait,
            )


@final
class Migrations:
    """One service's migration scripts, and what runs them.

    ``scripts`` is a package resource (``package:directory``) or a directory; they are one line of history,
    with one head. ``owner`` names the service in the messages of the errors.
    """

    __slots__ = ("_owner", "_scripts")

    def __init__(self, scripts: str, *, owner: str) -> None:
        self._scripts = scripts
        self._owner = owner

    def config(self) -> Config:
        """Alembic's configuration of the scripts, with no database: for Alembic's own commands in tests."""
        config = Config()
        config.set_main_option("script_location", self._scripts)
        return config

    def _targeting(self, url: str, lock_wait: timedelta) -> Config:
        config = self.config()
        # An attribute, not an option: the option parser would read a % in the password as interpolation.
        config.attributes[_TARGET] = Target(url=url, owner=self._owner, lock_wait=lock_wait)
        return config

    def upgrade(self, url: str, revision: str = HEAD, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None:
        """Bring the database at ``url`` (``postgresql+psycopg://``) up to ``revision``.

        Raises ``MigrationLockTimeoutError`` when another migration holds the database for longer than
        ``lock_wait``.
        """
        command.upgrade(self._targeting(url, lock_wait), revision)

    def downgrade(
        self, url: str, revision: str = BASE, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT
    ) -> None:
        """Take the database at ``url`` down to ``revision``, after any other migration (see ``upgrade``)."""
        command.downgrade(self._targeting(url, lock_wait), revision)

    def head(self) -> str:
        """The revision this version's migrations end at: the schema its code expects."""
        (current,) = ScriptDirectory.from_config(self.config()).get_heads()  # one line of history, one head
        return current

    def revisions(self) -> frozenset[str]:
        """Every revision this version's scripts know."""
        return frozenset(
            script.revision for script in ScriptDirectory.from_config(self.config()).walk_revisions()
        )

    def readiness(self) -> SchemaReadiness:
        """The running service's readiness check of its schema (see ``SchemaReadiness``)."""
        return SchemaReadiness(head=self.head(), known=self.revisions())


@final
class SchemaReadiness:
    """Whether a database's schema serves this version: a readiness check, called with the database.

    Ready when the database is at ``head``, or at a revision ``known`` lacks: a newer version migrated it
    during a rollout, and a schema only grows, so this version keeps working on it. Not ready
    (``SchemaNotCurrentError``) when it is at an older revision of ``known``, at the base, or was never
    migrated. It reads on a connection of its own (``Database.probing``), never the pool's, and once it has
    passed it passes without asking: no migration takes a schema back while a version that needs it runs.
    """

    __slots__ = ("_head", "_older", "_passed")

    def __init__(self, *, head: str, known: frozenset[str]) -> None:
        self._head = head
        self._older = known - {head}
        self._passed = False

    async def __call__(self, database: Database) -> None:
        """Return when the schema serves this version, else raise ``SchemaNotCurrentError``."""
        if self._passed:
            return
        async with database.probing() as connection:
            recorded = (await connection.execute(_RECORDED)).scalar_one()
            found: frozenset[str] = (
                frozenset((await connection.execute(_VERSION)).scalars()) if recorded else frozenset()
            )
        if not found or found & self._older:
            raise SchemaNotCurrentError(self._head)
        self._passed = True


def _lock_timeout(connection: Connection, value: str) -> None:
    """Set ``lock_timeout`` until the transaction ends, as ``SET LOCAL`` does."""
    connection.execute(select(func.set_config("lock_timeout", value, true())))


def _serialized(connection: Connection, target: Target) -> None:
    """Wait at most ``target.lock_wait`` for any other migration to commit, then bound other waits again.

    The wait, positive (``Target`` checks it), is rounded up to a whole millisecond, the unit of
    ``lock_timeout``: never down to zero, which would mean no bound at all.
    """
    _lock_timeout(connection, f"{math.ceil(target.lock_wait / _MILLISECOND)}ms")
    try:
        connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK)))
    except OperationalError as error:
        if isinstance(error.orig, psycopg.errors.LockNotAvailable):
            raise MigrationLockTimeoutError(target.owner, target.lock_wait) from error
        raise
    _lock_timeout(connection, _LOCK_TIMEOUT)


def run_migrations(*metadata: MetaData) -> None:
    """The body of a service's ``env.py``: its revisions, on the target ``Migrations`` handed over.

    In one transaction, one migration of the database at a time. ``metadata`` (the service's tables, beside
    the chassis's) is what Alembic compares the schema with.
    """
    attributes: Mapping[str, object] = context.config.attributes
    if context.is_offline_mode():
        raise OfflineMigrationsError
    target = attributes.get(_TARGET)
    if not isinstance(target, Target):
        raise MigrationUrlMissingError
    engine = create_engine(
        target.url, poolclass=NullPool, connect_args={"options": f"-c lock_timeout={_LOCK_TIMEOUT}"}
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=[CHASSIS_METADATA, *metadata])
            with context.begin_transaction():
                _serialized(connection, target)
                context.run_migrations()
    finally:
        engine.dispose()


def rows_by_scope(
    connection: Connection,
    of: AnyTable,
    where: ColumnElement[bool] | None = None,
    *,
    organization_column: str = "organization_id",
) -> dict[Scope, int]:
    """How many rows of ``of`` match ``where``, by the scope that owns them, read past row-level security.

    For a data migration, inside its transaction, as the table's owner (who runs migrations). A table with a
    ``company_id`` is counted by ``Tenant``, any other by the organization in ``organization_column``. The
    policy is lifted for this one read: the owner is subject to it only because it is forced, and the force
    is put back before the function returns (and by the rollback if the read fails), so the statements that
    follow run under it again. ``run_per_scope`` takes the answer as what each scope's statement must change.
    """
    name = ddl_name(of)
    forced = connection.execute(_FORCED, {"table": name}).scalar_one() is True
    organization = type_coerce(of.c[organization_column], Uuid())
    if forced:
        connection.execute(text(f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY"))
    if "company_id" in of.c:
        company = type_coerce(of.c.company_id, Uuid())
        by_tenant = select(organization, company, func.count()).group_by(organization, company)
        counted = connection.execute(by_tenant if where is None else by_tenant.where(where)).all()
        counts: dict[Scope, int] = {
            Tenant(OrganizationId(owner), CompanyId(unit)): rows for owner, unit, rows in counted
        }
    else:
        by_organization = select(organization, func.count()).group_by(organization)
        statement = by_organization if where is None else by_organization.where(where)
        counts = {OrganizationId(owner): rows for owner, rows in connection.execute(statement).all()}
    if forced:
        connection.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
    return counts


def run_per_scope(connection: Connection, statement: Update | Delete, expected: Mapping[Scope, int]) -> None:
    """Run ``statement`` once in each scope of ``expected``, which says how many rows it must change there.

    ``statement`` is an ``UPDATE`` or a ``DELETE``. Row-level security confines each run to its scope. A run
    that changes another number raises ``UnexpectedRowCountError``, which fails the migration and rolls it
    back. Once every run has, the transaction is left with no scope.
    """
    for scope, rows in expected.items():
        connection.execute(SET_SCOPE, scope_settings(scope))
        changed = connection.execute(statement).rowcount
        if changed != rows:
            raise UnexpectedRowCountError(scope=scope, expected=rows, changed=changed)
    connection.execute(SET_SCOPE, scope_settings(None))
