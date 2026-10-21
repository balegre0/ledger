"""Schema ``do_ecf``: its revisions, run by ``ledger-migrate`` as ``do_ecf_owner`` (§6.5)."""

from typing import Final

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import SCHEMA, DoEcfTable
from ledger_platform.data import SchemaMigrations


def migrations() -> SchemaMigrations:
    """The revisions of the channel's schema (``migrations/versions``), each run in its own transaction.

    One per app: its readiness, once met, is remembered, and two apps may be on two databases.
    """
    return SchemaMigrations(
        schema=SCHEMA,
        scripts="ledger_country_do.channels.ecf_v1.adapters.postgres:migrations",
        metadata=DoEcfTable.metadata,
    )


MIGRATIONS: Final = migrations()
"""The channel's revisions (one shared instance, for tests and tools)."""
