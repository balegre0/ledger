import subprocess
import sys
from typing import Final

from ledger_platform.schema import CHASSIS_METADATA, CHASSIS_POLICIES

IMPORT_ONLY_THE_SCHEMA: Final = (
    "import ledger_platform.schema as schema; print(*sorted(schema.CHASSIS_METADATA.tables))"
)
"""All a migration imports of the chassis: the tables must be there with nothing else imported."""


def test_the_chassis_owns_the_outbox_the_inbox_and_the_idempotency_keys_and_nothing_else() -> None:
    assert set(CHASSIS_METADATA.tables) == {"outbox", "inbox", "idempotency_keys"}


def test_the_indexes_the_relay_and_the_purge_read_are_part_of_the_schema() -> None:
    indexes = {
        name: {index.name for index in table.indexes} for name, table in CHASSIS_METADATA.tables.items()
    }

    assert indexes == {
        "outbox": {"ix_outbox_due", "ix_outbox_stored_at"},
        "inbox": set(),
        "idempotency_keys": {"ix_idempotency_keys_expiry"},
    }


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert [column.name for column in CHASSIS_METADATA.tables["inbox"].primary_key] == [
        "consumer",
        "event_id",
    ]


def test_the_idempotency_keys_are_keyed_by_organization_subject_and_key() -> None:
    assert [column.name for column in CHASSIS_METADATA.tables["idempotency_keys"].primary_key] == [
        "organization_id",
        "subject",
        "key",
    ]


def test_only_the_idempotency_keys_are_under_row_level_security() -> None:
    rule = "organization_id = NULLIF(current_setting('ledger.organization_id', true), '')::uuid"
    assert (
        "ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS organization_isolation ON idempotency_keys",
        f"CREATE POLICY organization_isolation ON idempotency_keys USING ({rule}) WITH CHECK ({rule})",
    ) == CHASSIS_POLICIES


def test_importing_the_schema_alone_registers_every_table() -> None:
    """In a process of its own: the tests here have imported the module of every table already."""
    imported = subprocess.run(
        [sys.executable, "-I", "-c", IMPORT_ONLY_THE_SCHEMA], check=True, capture_output=True, text=True
    )

    assert imported.stdout.split() == ["idempotency_keys", "inbox", "outbox"]
