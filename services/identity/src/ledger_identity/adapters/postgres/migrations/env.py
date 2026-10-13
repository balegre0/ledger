"""Alembic's entry point: identity's migrations on the URL ``migrate`` hands over, in one transaction.

Every session of a migration waits at most ten seconds for a lock: a migration that cannot get one fails, it
never queues the service's own statements behind it.
"""

from typing import Final
lazy from collections.abc import Mapping

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from ledger_identity.adapters.postgres.migrate import MigrationUrlMissingError, OfflineMigrationsError
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.schema import CHASSIS_METADATA

_LOCK_TIMEOUT: Final = "-c lock_timeout=10s"


def _run() -> None:
    if context.is_offline_mode():
        raise OfflineMigrationsError
    attributes: Mapping[str, object] = context.config.attributes
    url = attributes.get("url")
    if not isinstance(url, str):
        raise MigrationUrlMissingError
    engine = create_engine(url, poolclass=NullPool, connect_args={"options": _LOCK_TIMEOUT})
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=[CHASSIS_METADATA, IdentityTable.metadata]
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


_run()
