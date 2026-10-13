"""identity's schema brought to this version's (Alembic), from the tests and from the command line.

Migrations run on a synchronous psycopg engine: no event loop is needed, so the same code runs in a test,
in the container that migrates before the service starts, and on a laptop. Each upgrade or downgrade is one
transaction, and waits for any other migration of the database first (``MIGRATION_LOCK``), for
``MIGRATION_LOCK_WAIT`` at most. ``require_head`` is the running service's side: its readiness check that the
database is at the revision this version's migrations end at.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Final, final

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String, column, func, select, table

from ledger_identity.settings import IdentitySettings
from ledger_kernel.errors import LedgerError, UnavailableError
lazy from ledger_platform.database import Database

SCRIPTS: Final = "ledger_identity.adapters.postgres:migrations"
"""Where the migration scripts are, as a package resource: they ship inside the wheel and the image."""
HEAD: Final = "head"
BASE: Final = "base"
_VERSION_TABLE: Final = "public.alembic_version"
_RECORDED: Final = select(func.to_regclass(_VERSION_TABLE).is_not(None))
"""Whether Alembic's table exists: a database no migration ever ran on has none."""
_VERSION: Final = select(column("version_num", String)).select_from(table("alembic_version", schema="public"))
"""The revision Alembic recorded: one row at a revision, none at the base."""
MIGRATION_LOCK: Final = 0x6C65646765720001
"""The advisory lock a migration holds until it commits: ``ledger`` in ASCII, then the lock's number.

Advisory locks are per database, and identity's database is its own.
"""
MIGRATION_LOCK_WAIT: Final = timedelta(minutes=10)
"""How long a migration waits for another one to commit: far longer than any of identity's migrations takes,
so that a replica starting while another migrates waits for it; yet finite, so that a migration stuck (or a
session that took the lock and never let go) fails the next one loudly instead of hanging it forever."""


class OfflineMigrationsError(LedgerError):
    """Someone asked for the migrations as SQL text. They run against a database only."""

    code: ClassVar[str] = "offline_migrations"

    def __init__(self) -> None:
        super().__init__("identity's migrations run against a database, not as SQL text")


class MigrationUrlMissingError(LedgerError):
    """The migrations were started without a database to run against."""

    code: ClassVar[str] = "migration_url_missing"

    def __init__(self) -> None:
        super().__init__("identity's migrations were started without a database URL")


class MigrationLockTimeoutError(UnavailableError):
    """Another migration held the migration lock for longer than this one would wait."""

    code: ClassVar[str] = "migration_lock_timeout"

    def __init__(self, waited: timedelta) -> None:
        super().__init__(f"another migration of identity's database held its lock for longer than {waited}")


class SchemaNotCurrentError(UnavailableError):
    """The database is not at the revision this version needs: migrations are pending (or ran ahead)."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, expected: str) -> None:
        super().__init__(f"the database schema is not at revision {expected}, the one this version needs")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    """What the migration environment runs against: the database, and how long to wait for its lock."""

    url: str = field(repr=False)  # it carries the password
    lock_wait: timedelta


TARGET: Final = "target"
"""The key of the ``Target`` among the attributes of Alembic's configuration."""


def _scripts() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPTS)
    return config


def _config(url: str, lock_wait: timedelta) -> Config:
    config = _scripts()
    # An attribute, not an option: the option parser would read a % in the password as interpolation.
    config.attributes[TARGET] = Target(url=url, lock_wait=lock_wait)
    return config


def upgrade(url: str, revision: str = HEAD, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None:
    """Bring the database at ``url`` (``postgresql+psycopg://``) up to ``revision``.

    Raises ``MigrationLockTimeoutError`` when another migration holds the database for longer than
    ``lock_wait``.
    """
    command.upgrade(_config(url, lock_wait), revision)


def downgrade(url: str, revision: str = BASE, *, lock_wait: timedelta = MIGRATION_LOCK_WAIT) -> None:
    """Take the database at ``url`` down to ``revision``, after any other migration, as ``upgrade`` does."""
    command.downgrade(_config(url, lock_wait), revision)


def head() -> str:
    """The revision this version's migrations end at: the schema its code expects."""
    (current,) = ScriptDirectory.from_config(_scripts()).get_heads()  # one line of history, one head
    return current


async def require_head(database: Database, expected: str) -> None:
    """Return when the database is at revision ``expected``, else raise ``SchemaNotCurrentError``.

    A database no migration ever ran on has no ``alembic_version`` table: it is not current either.
    """
    found: list[str] = []
    async with database.transaction(None) as unit:
        if (await unit.connection.execute(_RECORDED)).scalar_one():
            found = list((await unit.connection.execute(_VERSION)).scalars())
    if found != [expected]:
        raise SchemaNotCurrentError(expected)


def main() -> int:
    """``ledger-identity-migrate``: bring the database of ``LEDGER_DATABASE_URL`` to this version's schema."""
    upgrade(IdentitySettings().database_url.get_secret_value())
    return 0
