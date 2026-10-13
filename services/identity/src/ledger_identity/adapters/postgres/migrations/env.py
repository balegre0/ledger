"""Alembic's entry point: identity's migrations on the target ``migrate`` hands over, in one transaction.

Migrations run one at a time: each first takes a transaction-level advisory lock, so a replica that starts
while another migrates waits for it, then finds the database at head and does nothing. That wait has a bound
of its own, long but finite (``MIGRATION_LOCK_WAIT``); every other lock is waited for at most ten seconds: a
migration that cannot get one fails, it never queues the service's own statements behind it.
"""

import math
from datetime import timedelta
from typing import Final
lazy from collections.abc import Mapping

import psycopg
from alembic import context
from sqlalchemy import create_engine, func, select, true
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Connection

from ledger_identity.adapters.postgres.migrate import (
    MIGRATION_LOCK,
    TARGET,
    MigrationLockTimeoutError,
    MigrationUrlMissingError,
    OfflineMigrationsError,
    Target,
)
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.schema import CHASSIS_METADATA

_LOCK_TIMEOUT: Final = "10s"
_MILLISECOND: Final = timedelta(milliseconds=1)


def _lock_timeout(connection: Connection, value: str) -> None:
    """Set ``lock_timeout`` until the transaction ends, as ``SET LOCAL`` does."""
    connection.execute(select(func.set_config("lock_timeout", value, true())))


def _serialized(connection: Connection, wait: timedelta) -> None:
    """Wait at most ``wait`` for any other migration to commit, then bound every other wait again.

    ``wait`` is rounded up to a whole millisecond, the unit of ``lock_timeout``, and to one at least: a zero
    ``lock_timeout`` would mean no bound at all.
    """
    _lock_timeout(connection, f"{max(1, math.ceil(wait / _MILLISECOND))}ms")
    try:
        connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK)))
    except OperationalError as error:
        if isinstance(error.orig, psycopg.errors.LockNotAvailable):
            raise MigrationLockTimeoutError(wait) from error
        raise
    _lock_timeout(connection, _LOCK_TIMEOUT)


def _run() -> None:
    if context.is_offline_mode():
        raise OfflineMigrationsError
    attributes: Mapping[str, object] = context.config.attributes
    target = attributes.get(TARGET)
    if not isinstance(target, Target):
        raise MigrationUrlMissingError
    engine = create_engine(
        target.url, poolclass=NullPool, connect_args={"options": f"-c lock_timeout={_LOCK_TIMEOUT}"}
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=[CHASSIS_METADATA, IdentityTable.metadata]
            )
            with context.begin_transaction():
                _serialized(connection, target.lock_wait)
                context.run_migrations()
    finally:
        engine.dispose()


_run()
