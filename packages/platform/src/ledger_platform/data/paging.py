"""A list's statement for the page a request asks for (§9.7): the one way every module's repositories page.

Rows are keyed by a UUIDv7 column (time-ordered). A page after a cursor (``starting_after``), or the first,
is read forwards in key order; a page before a cursor (``ending_before``) is read backwards, newest first, so
that the rows nearest the cursor are the ones kept. Either way one row more than the page holds is read: it
only says that more lie that way. ``ledger_kernel.pagination.page_of`` turns the rows into the page, in key
order.
"""

lazy from uuid import UUID

lazy from sqlalchemy import Select, SQLColumnExpression

lazy from ledger_kernel.pagination import PageRequest


def paged[*Columns](
    statement: Select[*Columns], key: SQLColumnExpression[UUID], request: PageRequest
) -> Select[*Columns]:
    """``statement`` limited to ``request``'s page and the row past it, in the order they are read in."""
    if request.before is not None:
        return statement.where(key < request.before).order_by(key.desc()).limit(request.size + 1)
    after = statement if request.after is None else statement.where(key > request.after)
    return after.order_by(key).limit(request.size + 1)
