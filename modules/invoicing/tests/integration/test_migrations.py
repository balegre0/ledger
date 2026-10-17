"""invoicing's schema as migrated: the chassis's tables, at the head of its revisions."""

from typing import Final

from pydantic import TypeAdapter
lazy import psycopg

from ledger_invoicing.adapters.postgres.schema import MIGRATIONS
from ledger_invoicing.adapters.postgres.tables import SCHEMA
from ledger_platform.data.tables import chassis_tables

TABLES: Final = TypeAdapter(list[tuple[str]])
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events", "alembic_version"}
)


async def test_the_schema_holds_the_chassis_s_tables_at_the_head_of_its_revisions(
    admin: psycopg.AsyncConnection,
) -> None:
    found = await admin.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (SCHEMA,))
    assert {name for (name,) in TABLES.validate_python(await found.fetchall())} >= CHASSIS_TABLES - {
        "alembic_version"
    }
    version = await admin.execute("SELECT version_num FROM invoicing.alembic_version")
    assert await version.fetchall() == [(MIGRATIONS.head,)]
    assert MIGRATIONS.head == "0002"  # the chassis's two versions, nothing else yet
    assert chassis_tables(SCHEMA).jobs.schema == SCHEMA
