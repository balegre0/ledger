"""Alembic's entry point for identity: the platform's runner, with identity's tables beside the chassis's."""

from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_platform.migrations import run_migrations

run_migrations(IdentityTable.metadata)
