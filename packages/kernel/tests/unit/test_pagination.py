lazy from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.ids import new_id
from ledger_kernel.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ConflictingCursorsError,
    InvalidCursorError,
    InvalidPageSizeError,
    Page,
    PageRequest,
    decode_cursor,
    encode_cursor,
    page_of,
)


def same(item: UUID) -> UUID:
    return item


@given(st.uuids())
def test_a_cursor_reads_back_as_the_id_it_was_made_from(value: UUID) -> None:
    assert decode_cursor(encode_cursor(value)) == value


def test_a_cursor_is_short_and_url_safe() -> None:
    cursor = encode_cursor(new_id())
    assert len(cursor) == 23
    assert all(character.isascii() and (character.isalnum() or character in "-_") for character in cursor)


@pytest.mark.parametrize(
    "text",
    ["", "not-a-cursor", "AQ", encode_cursor(new_id())[:-1], "A" * 23, "!" * 23, "ü" * 23, "x" * 10_000],
    ids=["empty", "word", "short", "cut", "other-version", "symbols", "non-ascii", "huge"],
)
def test_a_cursor_that_was_not_made_here_is_refused_without_being_echoed(text: str) -> None:
    with pytest.raises(InvalidCursorError, match="not a cursor") as caught:
        decode_cursor(text)
    assert len(str(caught.value)) < 80
    assert caught.value.code == "invalid_cursor"


def test_a_page_request_holds_from_one_to_the_maximum_items() -> None:
    assert PageRequest().size == DEFAULT_PAGE_SIZE == 50
    assert PageRequest(size=MAX_PAGE_SIZE).size == 100
    assert PageRequest(size=1).after is None


@pytest.mark.parametrize("size", [0, -1, MAX_PAGE_SIZE + 1])
def test_a_page_request_outside_the_range_is_refused(size: int) -> None:
    with pytest.raises(InvalidPageSizeError, match=f"not {size}") as caught:
        PageRequest(size=size)
    assert caught.value.code == "invalid_page_size"


def build(*, size: object) -> PageRequest:
    """A page request holding a size of any type, the way a caller that skipped the type checker makes one."""
    request = PageRequest()
    object.__setattr__(request, "size", size)
    request.__post_init__()
    return request


@pytest.mark.parametrize(
    ("size", "shown"),
    [(True, "bool: True"), (1.0, "float: 1.0"), ("5", "str: 5"), (None, "NoneType: None")],
    ids=["bool", "float", "text", "none"],
)
def test_a_page_size_that_is_not_an_integer_is_refused_and_described(size: object, shown: str) -> None:
    with pytest.raises(InvalidPageSizeError, match=f"not {shown}"):
        build(size=size)


def test_a_page_holds_at_most_the_size_asked_for_and_says_where_to_continue() -> None:
    ids = sorted(new_id() for _ in range(3))
    page = page_of(ids, PageRequest(size=2), key=same)
    assert page.items == (ids[0], ids[1])
    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == ids[1]


def test_the_last_page_says_nothing_follows() -> None:
    ids = [new_id(), new_id()]
    assert page_of(ids, PageRequest(size=2), key=same) == Page(items=(ids[0], ids[1]), next_cursor=None)


def test_an_empty_page() -> None:
    nothing: list[UUID] = []
    assert page_of(nothing, PageRequest(), key=same) == Page(items=(), next_cursor=None)


def test_a_page_says_whether_more_follow() -> None:
    ids = sorted(new_id() for _ in range(3))
    assert page_of(ids, PageRequest(size=2), key=same).has_more
    assert not page_of(ids, PageRequest(size=3), key=same).has_more


def test_a_page_read_backwards_holds_the_items_before_the_cursor_in_id_order() -> None:
    """``ending_before``: rows come newest first; the page holds them oldest first and continues before."""
    ids = sorted(new_id() for _ in range(4))
    request = PageRequest(size=2, before=ids[3])
    page = page_of([ids[2], ids[1], ids[0]], request, key=same)
    assert page.items == (ids[1], ids[2])
    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == ids[1]
    assert page_of([ids[1], ids[0]], PageRequest(size=2, before=ids[2]), key=same) == Page(
        items=(ids[0], ids[1]), next_cursor=None
    )


def test_a_page_cannot_start_after_one_id_and_end_before_another() -> None:
    with pytest.raises(ConflictingCursorsError) as caught:
        PageRequest(after=new_id(), before=new_id())
    assert caught.value.code == "page_cursors_conflict"
