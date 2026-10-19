"""signer's schema: its revisions, run by ``ledger-migrate`` as ``signer_owner`` (§6.5)."""

from typing import Final

from ledger_platform.data import SchemaMigrations
from ledger_signer.adapters.postgres.tables import SCHEMA, SignerTable


def migrations() -> SchemaMigrations:
    """The revisions of signer's schema (``migrations/versions``), each run in its own transaction.

    One per app: its readiness, once met, is remembered, and two apps may be on two databases.
    """
    return SchemaMigrations(
        schema=SCHEMA, scripts="ledger_signer.adapters.postgres:migrations", metadata=SignerTable.metadata
    )


MIGRATIONS: Final = migrations()
"""signer's revisions (one shared instance, for tests and tools)."""
