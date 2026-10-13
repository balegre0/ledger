"""What every module's list answers and asks (§9.7): one generic ``Page[T]``, and the request of a page.

A list is read with ``limit`` (1 to 100) and at most one opaque cursor: ``starting_after`` (the page after it)
or ``ending_before`` (the page before it). A page answers its items in id order, whether more follow in the
direction it was read (``has_more``), and the cursors of the pages on either side: ``next_cursor`` (for
``starting_after``) and ``previous_cursor`` (for ``ending_before``), each ``null`` when there is none. A
cursor is only ever one the service handed out: anything else is 422 ``invalid_cursor``, and both cursors at
once 422 ``page_cursors_conflict``.
"""

from typing import Annotated, ClassVar, Final
lazy from collections.abc import Callable

from fastapi import Query
from pydantic import BaseModel, ConfigDict

from ledger_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageRequest, decode_cursor
lazy from ledger_kernel.pagination import Page as KernelPage

_MAX_CURSOR_LENGTH: Final = 64
"""Longer than any cursor of ours; a longer text is refused unread."""


class Page[T](BaseModel):
    """A page of a list: its items, whether more follow, and the cursor of the next page (``null``: none)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: list[T]
    has_more: bool
    next_cursor: str | None
    """Read the page after this one with it (``starting_after``); ``null`` when nothing follows."""
    previous_cursor: str | None
    """Read the page before this one with it (``ending_before``); ``null`` when nothing comes before."""


def page_of_items[I, T](page: KernelPage[I], render: Callable[[I], T]) -> Page[T]:
    """The kernel's ``page`` as an answer, each item rendered by ``render``."""
    return Page[T](
        items=[render(item) for item in page.items],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
        previous_cursor=page.previous_cursor,
    )


async def page_query(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many items the page holds.")] = (
        DEFAULT_PAGE_SIZE
    ),
    starting_after: Annotated[
        str | None,
        Query(max_length=_MAX_CURSOR_LENGTH, description="Read the page after this cursor."),
    ] = None,
    ending_before: Annotated[
        str | None,
        Query(max_length=_MAX_CURSOR_LENGTH, description="Read the page before this cursor."),
    ] = None,
) -> PageRequest:
    """The page a list request asks for (§9.7)."""
    return PageRequest(
        size=limit,
        after=None if starting_after is None else decode_cursor(starting_after),
        before=None if ending_before is None else decode_cursor(ending_before),
    )
