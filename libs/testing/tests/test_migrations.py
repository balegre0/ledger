from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, Uuid, create_engine, text
from sqlalchemy.pool import NullPool

from ledger_kernel.ids import new_id
from ledger_testing.migrations import (
    check_constraints,
    combined,
    pending_changes,
    policies,
    primary_keys,
    row_security,
    tables_of,
)
lazy from ledger_testing.containers import Postgres


def models() -> tuple[MetaData, MetaData]:
    first, second = MetaData(), MetaData()
    Table(
        "notes",
        first,
        Column("id", Uuid(), primary_key=True),
        Column("organization_id", Uuid(), nullable=False),
        Column("size", Integer, nullable=False),
        CheckConstraint("size > 0", name="ck_notes_size"),
    )
    Table(
        "tags", second, Column("note_id", Uuid(), primary_key=True), Column("tag", Integer, primary_key=True)
    )
    return first, second


def migrated(server: Postgres, models: MetaData) -> str:
    name = f"schema_{new_id().hex[16:]}"
    url = server.database(name, role=name, password="schema-password")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            models.create_all(connection)
            connection.execute(text("ALTER TABLE notes ENABLE ROW LEVEL SECURITY"))
            connection.execute(text("ALTER TABLE notes FORCE ROW LEVEL SECURITY"))
            connection.execute(text("CREATE POLICY mine ON notes USING (true)"))
    finally:
        engine.dispose()
    return url


def test_a_schema_built_from_the_models_is_read_back_as_them(server: Postgres) -> None:
    both = combined(*models())
    url = migrated(server, both)
    assert tables_of(url) == {"notes", "tags"}
    assert pending_changes(url, both) is None
    assert check_constraints(url) == {"ck_notes_size": "CHECK ((size > 0))"}
    assert primary_keys(url) == {"notes": ["id"], "tags": ["note_id", "tag"]}
    assert policies(url) == [("notes", "mine")]
    assert row_security(url) == {"notes": (True, True), "tags": (False, False)}


def test_a_model_the_schema_lacks_is_a_pending_change(server: Postgres) -> None:
    first, second = models()
    url = migrated(server, first)
    assert "tags" in (pending_changes(url, combined(first, second)) or "")
