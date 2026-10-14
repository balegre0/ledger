from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from ledger_platform.data import ModuleTable
from ledger_platform.data.tables import UnscopedTableError


class BooksTable(ModuleTable, schema="books"):
    """A module's own base, named with its schema."""


class LedgerRecord(BooksTable):
    __tablename__: str = "ledgers"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StockTable(ModuleTable, schema="stock"):
    """Another module's."""


class ItemRecord(StockTable):
    __tablename__: str = "items"

    id: Mapped[UUID] = mapped_column(primary_key=True)


def test_a_module_s_tables_are_in_its_schema_and_its_metadata_alone() -> None:
    assert (LedgerRecord.__table__.schema, ItemRecord.__table__.schema) == ("books", "stock")
    assert LedgerRecord.opened_at.key == "opened_at"
    assert set(BooksTable.metadata.tables) == {"books.ledgers"}
    assert set(StockTable.metadata.tables) == {"stock.items"}
    assert BooksTable.metadata is not StockTable.metadata
    assert ModuleTable.metadata.tables == {}


def test_the_base_of_a_module_maps_no_table_of_its_own() -> None:
    assert "__table__" not in vars(BooksTable)
    assert BooksTable.__abstract__


def test_a_table_without_its_module_s_base_is_refused() -> None:
    with pytest.raises(UnscopedTableError, match="subclass their package's base"):
        type("Stray", (ModuleTable,), {"__tablename__": "stray"})
