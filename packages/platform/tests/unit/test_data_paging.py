import uuid
from typing import Final

from sqlalchemy import Column, MetaData, String, Table, Uuid, create_engine, select

from ledger_kernel.pagination import PageRequest
from ledger_platform.data.paging import paged

THINGS: Final = Table("things", MetaData(), Column("id", Uuid(), primary_key=True), Column("name", String()))
CURSOR: Final = uuid.UUID("01890000-0000-7000-8000-000000000001")
DIALECT: Final = create_engine("postgresql+psycopg://").dialect
"""PostgreSQL's, as the apps' engines have it (no connection is made)."""


def sql(request: PageRequest) -> str:
    """The statement ``paged`` makes for ``request``, as PostgreSQL is sent it (bound values inlined)."""
    statement = paged(select(THINGS.c.id, THINGS.c.name), THINGS.c.id, request)
    compiled = statement.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
    return " ".join(str(compiled).split())


def test_the_first_page_is_read_forwards_one_row_more_than_it_holds() -> None:
    assert sql(PageRequest(size=3)) == (
        "SELECT things.id, things.name FROM things ORDER BY things.id LIMIT 4"
    )


def test_a_page_after_a_cursor_reads_forwards_from_it() -> None:
    assert sql(PageRequest(size=2, after=CURSOR)) == (
        "SELECT things.id, things.name FROM things "
        "WHERE things.id > '01890000-0000-7000-8000-000000000001' ORDER BY things.id LIMIT 3"
    )


def test_a_page_before_a_cursor_reads_backwards_newest_first() -> None:
    """``page_of`` puts the rows read backwards back in id order: the statement reads them newest first."""
    assert sql(PageRequest(size=2, before=CURSOR)) == (
        "SELECT things.id, things.name FROM things "
        "WHERE things.id < '01890000-0000-7000-8000-000000000001' ORDER BY things.id DESC LIMIT 3"
    )
