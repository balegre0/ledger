"""Cursor pagination: a page of items in id order, and an opaque cursor saying where the next page starts.

Ids are UUIDv7, so id order is creation order. A page is read forwards (after an id: ``starting_after``) or
backwards (before one: ``ending_before``, §9.7); either way it holds its items in id order, says whether more
follow in the direction it was read (``has_more``), and its cursor is where the next page in that direction
starts: the last id it holds read forwards, the first read backwards. A cursor is opaque to clients (a version
byte and the id, base64url without padding), so its encoding can change; text that is not one of ours is
refused, and never echoed whole.
"""

import base64
import re
from dataclasses import dataclass
from typing import ClassVar, Final, final
from uuid import UUID
lazy from collections.abc import Callable, Sequence

from ledger_kernel.errors import InvalidInputError, described, excerpt

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


class ConflictingCursorsError(InvalidInputError):
    """A page asked to start after one id and end before another: one direction at a time."""

    code: ClassVar[str] = "page_cursors_conflict"

    def __init__(self) -> None:
        super().__init__("a page starts after a cursor or ends before one, not both")


class InvalidPageSizeError(InvalidInputError):
    """A page size outside 1..``MAX_PAGE_SIZE``."""

    code: ClassVar[str] = "invalid_page_size"

    def __init__(self, size: object) -> None:
        shown = str(size) if type(size) is int else described(size)
        super().__init__(f"a page holds 1 to {MAX_PAGE_SIZE} items, not {shown}")


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
    """How many items a page holds, and after which id it starts or before which it ends (neither: the first).

    A page that ends ``before`` an id is read backwards: its rows are read newest first.
    """

    size: int = DEFAULT_PAGE_SIZE
    after: UUID | None = None
    before: UUID | None = None

    def __post_init__(self) -> None:
        # ``type`` and not ``isinstance``: a bool is an int, and True is not a page size.
        if type(self.size) is not int or not 1 <= self.size <= MAX_PAGE_SIZE:
            raise InvalidPageSizeError(self.size)
        if self.after is not None and self.before is not None:
            raise ConflictingCursorsError


@final
@dataclass(frozen=True, slots=True)
class Page[T]:
    """Items in id order, and the cursors of the pages on either side of it (``None`` where there is none).

    ``next_cursor`` starts the page after the last item (``starting_after``), ``previous_cursor`` ends the
    page before the first (``ending_before``); ``has_more`` says whether more follow in the direction read.
    """

    items: tuple[T, ...]
    next_cursor: str | None
    previous_cursor: str | None = None
    backwards: bool = False
    """Whether the page was read backwards (``ending_before``)."""

    @property
    def has_more(self) -> bool:
        """Whether more items follow in the direction the page was read."""
        return (self.previous_cursor if self.backwards else self.next_cursor) is not None


def page_of[T](rows: Sequence[T], request: PageRequest, *, key: Callable[[T], UUID]) -> Page[T]:
    """The page ``request`` asked for, from ``rows`` read in its direction: ``request.size`` + 1 at most.

    Rows are in id order, or newest first for a page read backwards (``request.before``). The extra row, when
    it is there, only says that more follow in that direction. Against it, something lies past the cursor the
    page was read from: read forwards, a page has a ``previous_cursor`` only after a cursor; backwards, it
    always has a ``next_cursor``.
    """
    kept = rows[: request.size]
    more = len(rows) > request.size
    backwards = request.before is not None
    items = tuple(reversed(kept)) if backwards else tuple(kept)
    if not items:
        return Page(items, None, None, backwards=backwards)
    first, last = encode_cursor(key(items[0])), encode_cursor(key(items[-1]))
    if backwards:
        return Page(items, last, first if more else None, backwards=True)
    return Page(items, last if more else None, first if request.after is not None else None)
