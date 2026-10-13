from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, Uuid, create_engine, text
from sqlalchemy.pool import NullPool

from ledger_kernel.ids import new_id
from ledger_testing.migrations import (
    check_constraints,
    combined,
    indexes_of,
    pending_changes,
    policies,
    primary_keys,
    row_security,
    tables_of,
)
lazy from ledger_testing.postgres import PostgresServer


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


def migrated(postgres: PostgresServer, models: MetaData) -> str:
    name = f"schema_{new_id().hex[16:]}"
    url = postgres.database(name, role=name, password="schema-password")
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


def test_a_schema_built_from_the_models_is_read_back_as_them(postgres: PostgresServer) -> None:
    both = combined(*models())
    url = migrated(postgres, both)
    assert tables_of(url) == {"notes", "tags"}
    assert pending_changes(url, both) is None
    assert check_constraints(url) == {"ck_notes_size": "CHECK ((size > 0))"}
    assert primary_keys(url) == {"notes": ["id"], "tags": ["note_id", "tag"]}
    assert indexes_of(url, "tags") == {
        "tags_pkey": "CREATE UNIQUE INDEX tags_pkey ON public.tags USING btree (note_id, tag)"
    }
    assert policies(url) == [("notes", "mine")]
    assert row_security(url) == {"notes": (True, True), "tags": (False, False)}


def test_a_model_the_schema_lacks_is_a_pending_change(postgres: PostgresServer) -> None:
    first, second = models()
    url = migrated(postgres, first)
    assert "tags" in (pending_changes(url, combined(first, second)) or "")


def test_the_readers_read_the_schema_they_are_told(postgres: PostgresServer) -> None:
    """A v2 module's tables live in a schema of its own: the same readers look there, and only there."""
    models = MetaData(schema="books")
    Table(
        "shelves",
        models,
        Column("id", Uuid(), primary_key=True),
        Column("size", Integer, nullable=False),
        CheckConstraint("size > 0", name="ck_shelves_size"),
    )
    name = f"schema_{new_id().hex[16:]}"
    url = postgres.database(name, role=name, password="schema-password")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA books"))
            models.create_all(connection)
            connection.execute(text("ALTER TABLE books.shelves ENABLE ROW LEVEL SECURITY"))
            connection.execute(text("CREATE POLICY mine ON books.shelves USING (true)"))
    finally:
        engine.dispose()
    assert tables_of(url, schema="books") == {"shelves"}
    assert check_constraints(url, schema="books") == {"ck_shelves_size": "CHECK ((size > 0))"}
    assert primary_keys(url, schema="books") == {"shelves": ["id"]}
    assert indexes_of(url, "shelves", schema="books") == {
        "shelves_pkey": "CREATE UNIQUE INDEX shelves_pkey ON books.shelves USING btree (id)"
    }
    assert policies(url, schema="books") == [("shelves", "mine")]
    assert row_security(url, schema="books") == {"shelves": (True, False)}
    assert (tables_of(url), policies(url)) == (set(), [])  # nothing of it in public
    assert pending_changes(url, models, schema="books") is None
    Table("labels", models, Column("shelf_id", Uuid(), primary_key=True), schema="books")
    assert "labels" in (pending_changes(url, models, schema="books") or "")
