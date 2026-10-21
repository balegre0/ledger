"""fiscal's schema: its revisions, run by ``ledger-migrate`` as ``fiscal_owner`` (§6.5)."""

from typing import Final

from ledger_fiscal.adapters.postgres.tables import SCHEMA, FiscalTable
from ledger_platform.data import SchemaMigrations


def migrations() -> SchemaMigrations:
    """The revisions of fiscal's schema (``migrations/versions``), each run in its own transaction.

    One per app: its readiness, once met, is remembered, and two apps may be on two databases.
    """
    return SchemaMigrations(
        schema=SCHEMA, scripts="ledger_fiscal.adapters.postgres:migrations", metadata=FiscalTable.metadata
    )


MIGRATIONS: Final = migrations()
"""fiscal's revisions (one shared instance, for tests and tools)."""
