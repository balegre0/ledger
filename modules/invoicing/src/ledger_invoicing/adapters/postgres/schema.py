"""invoicing's schema: its revisions, run by ``ledger-migrate`` as ``invoicing_owner`` (§6.5)."""

from typing import Final

from ledger_invoicing.adapters.postgres.tables import SCHEMA, InvoicingTable
from ledger_platform.data import SchemaMigrations


def migrations() -> SchemaMigrations:
    """The revisions of invoicing's schema (``migrations/versions``), each run in its own transaction.

    One per app: its readiness, once met, is remembered, and two apps may be on two databases.
    """
    return SchemaMigrations(
        schema=SCHEMA,
        scripts="ledger_invoicing.adapters.postgres:migrations",
        metadata=InvoicingTable.metadata,
    )


MIGRATIONS: Final = migrations()
"""invoicing's revisions (one shared instance, for tests and tools)."""
