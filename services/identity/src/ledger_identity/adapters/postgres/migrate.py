"""identity's schema brought to this version's (Alembic), from the tests and from the command line.

Migrations run on a synchronous psycopg engine: no event loop is needed, so the same code runs in a test,
in the container that migrates before the service starts, and on a laptop. Each upgrade or downgrade is one
transaction. ``require_head`` is the running service's side: its readiness check that the database is at the
revision this version's migrations end at.
"""

from typing import ClassVar, Final

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


class SchemaNotCurrentError(UnavailableError):
    """The database is not at the revision this version needs: migrations are pending (or ran ahead)."""

    code: ClassVar[str] = "schema_not_current"

    def __init__(self, expected: str) -> None:
        super().__init__(f"the database schema is not at revision {expected}, the one this version needs")


def _scripts() -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPTS)
    return config


def _config(url: str) -> Config:
    config = _scripts()
    # An attribute, not an option: the option parser would read a % in the password as interpolation.
    config.attributes["url"] = url
    return config


def upgrade(url: str, revision: str = HEAD) -> None:
    """Bring the database at ``url`` (``postgresql+psycopg://``) up to ``revision``."""
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = BASE) -> None:
    """Take the database at ``url`` down to ``revision``."""
    command.downgrade(_config(url), revision)


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
