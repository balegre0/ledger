"""Cursor pagination: a page of items in id order, and an opaque cursor saying where the next page starts.

Ids are UUIDv7, so id order is creation order, and a cursor is the last id a page held: the next page holds
the items after it. A cursor is opaque to clients (a version byte and the id, base64url without padding), so
its encoding can change; text that is not one of ours is refused, and never echoed whole.
"""

import base64
import re
from dataclasses import dataclass
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Callable, Sequence

from ledger_kernel.errors import InvalidInputError, excerpt

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 100
_VERSION: Final = b"\x01"
_CURSOR: Final = re.compile(r"[A-Za-z0-9_-]{23}", re.ASCII)
"""Seventeen bytes in base64url without padding: the version byte and the sixteen of the id."""


class InvalidCursorError(InvalidInputError):
    """Text that is not a cursor this service handed out."""

    code: ClassVar[str] = "invalid_cursor"

    def __init__(self, text: str) -> None:
        super().__init__(f"not a cursor: {excerpt(text)}")


class InvalidPageSizeError(InvalidInputError):
    """A page size outside 1..``MAX_PAGE_SIZE``."""

    code: ClassVar[str] = "invalid_page_size"

    def __init__(self, size: int) -> None:
        super().__init__(f"a page holds 1 to {MAX_PAGE_SIZE} items, not {size}")


def encode_cursor(after: UUID) -> str:
    """The cursor of the page that starts after ``after``."""
    return base64.urlsafe_b64encode(_VERSION + after.bytes).rstrip(b"=").decode("ascii")


def decode_cursor(text: str) -> UUID:
    """The id a cursor of ours continues after; anything else is an ``InvalidCursorError``."""
    if _CURSOR.fullmatch(text) is None:
        raise InvalidCursorError(text)
    raw = base64.urlsafe_b64decode(text + "=")
    if raw[:1] != _VERSION:
        raise InvalidCursorError(text)
    return UUID(bytes=raw[1:])


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PageRequest:
    """How many items a page holds, and after which id it starts (``None``: from the first)."""

    size: int = DEFAULT_PAGE_SIZE
    after: UUID | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.size <= MAX_PAGE_SIZE:
            raise InvalidPageSizeError(self.size)


@final
@dataclass(frozen=True, slots=True)
class Page[T]:
    """Items in id order, and the cursor of the next page (``None`` on the last)."""

    items: tuple[T, ...]
    next_cursor: str | None


def page_of[T](rows: Sequence[T], request: PageRequest, *, key: Callable[[T], UUID]) -> Page[T]:
    """The page ``request`` asked for, from ``rows`` read in id order, one more than ``request.size`` at most.

    The extra row, when it is there, only says that more follow: the cursor is the id of the last item kept.
    """
    items = tuple(rows[: request.size])
    more = len(rows) > request.size
    return Page(items, encode_cursor(key(items[-1])) if more else None)
