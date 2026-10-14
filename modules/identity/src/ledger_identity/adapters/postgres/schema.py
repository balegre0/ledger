"""identity's schema: its revisions, run by ``ledger-migrate`` as ``identity_owner`` (§6.5)."""

from typing import Final

from ledger_identity.adapters.postgres.tables import SCHEMA, IdentityTable
from ledger_platform.data import SchemaMigrations


def migrations() -> SchemaMigrations:
    """The revisions of identity's schema (``migrations/versions``), each run in its own transaction.

    One per app: its readiness, once met, is remembered, and two apps may be on two databases.
    """
    return SchemaMigrations(
        schema=SCHEMA, scripts="ledger_identity.adapters.postgres:migrations", metadata=IdentityTable.metadata
    )


MIGRATIONS: Final = migrations()
"""identity's revisions (one shared instance, for tests and tools)."""
