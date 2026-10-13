"""Response models every module's API shares (§9.7): one generic ``Page[T]`` for every list."""

from typing import ClassVar
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

lazy from ledger_kernel.pagination import Page as KernelPage


class Page[T](BaseModel):
    """A page of a list: its items and the cursor of the next page (``null`` on the last)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: list[T]
    next_cursor: str | None


def page_of_items[I, T](page: KernelPage[I], render: Callable[[I], T]) -> Page[T]:
    """The kernel's ``page`` as an answer, each item rendered by ``render``."""
    return Page[T](items=[render(item) for item in page.items], next_cursor=page.next_cursor)
