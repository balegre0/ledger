"""Alembic's entry point: identity's migrations on the URL ``migrate`` hands over, in one transaction.

Migrations run one at a time: each first takes a transaction-level advisory lock, so a replica that starts
while another migrates waits for it, then finds the database at head and does nothing. Every lock but that one
is waited for at most ten seconds: a migration that cannot get one fails, it never queues the service's own
statements behind it.
"""

from typing import Final
lazy from collections.abc import Mapping

from alembic import context
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection

from ledger_identity.adapters.postgres.migrate import MigrationUrlMissingError, OfflineMigrationsError
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.schema import CHASSIS_METADATA

_LOCK_TIMEOUT: Final = "10s"
MIGRATION_LOCK: Final = 0x6C65646765720001
"""The advisory lock a migration holds until it commits: ``ledger`` in ASCII, then the lock's number.

Advisory locks are per database, and identity's database is its own. Waiting for it is not bounded: another
migration may take longer than any ``lock_timeout``, and the one that waits has nothing to do until it ends.
"""


_UNBOUNDED: Final = text("SET LOCAL lock_timeout = 0")
_BOUNDED: Final = text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")


def _serialized(connection: Connection) -> None:
    """Wait without a timeout for any other migration to commit, then bound every other wait again."""
    connection.execute(_UNBOUNDED)
    connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK)))
    connection.execute(_BOUNDED)


def _run() -> None:
    if context.is_offline_mode():
        raise OfflineMigrationsError
    attributes: Mapping[str, object] = context.config.attributes
    url = attributes.get("url")
    if not isinstance(url, str):
        raise MigrationUrlMissingError
    engine = create_engine(
        url, poolclass=NullPool, connect_args={"options": f"-c lock_timeout={_LOCK_TIMEOUT}"}
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=[CHASSIS_METADATA, IdentityTable.metadata]
            )
            with context.begin_transaction():
                _serialized(connection)
                context.run_migrations()
    finally:
        engine.dispose()


_run()
