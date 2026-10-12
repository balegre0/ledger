import subprocess
import sys
from typing import Final

from ledger_platform.schema import CHASSIS_METADATA

IMPORT_ONLY_THE_SCHEMA: Final = (
    "import ledger_platform.schema as schema; print(*sorted(schema.CHASSIS_METADATA.tables))"
)
"""All a migration imports of the chassis: the tables must be there with nothing else imported."""


def test_the_chassis_owns_the_outbox_and_the_inbox_and_nothing_else() -> None:
    assert set(CHASSIS_METADATA.tables) == {"outbox", "inbox"}


def test_the_relay_s_index_is_part_of_the_schema() -> None:
    indexes = {
        name: {index.name for index in table.indexes} for name, table in CHASSIS_METADATA.tables.items()
    }

    assert indexes == {"outbox": {"ix_outbox_due"}, "inbox": set()}


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert [column.name for column in CHASSIS_METADATA.tables["inbox"].primary_key] == [
        "consumer",
        "event_id",
    ]


def test_importing_the_schema_alone_registers_both_tables() -> None:
    """In a process of its own: here the tests have imported the outbox and the consumers already."""
    imported = subprocess.run(
        [sys.executable, "-I", "-c", IMPORT_ONLY_THE_SCHEMA], check=True, capture_output=True, text=True
    )

    assert imported.stdout.split() == ["inbox", "outbox"]
