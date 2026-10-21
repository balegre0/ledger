from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError
from ledger_country_do.channels.ecf_v1.domain.polling import (
    POLL_EVERY,
    POLL_SCHEDULE,
    RECEIPT_TOLERANCE,
    SETTLE_DELAY,
    TRACK_ID_WINDOW,
    TrackId,
    adoptable,
    next_poll,
)
from ledger_kernel.errors import InvalidValueError

SIGNED_AT: Final = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


def test_the_timings_are_the_spec_s() -> None:
    spec = ((1, 2, 5, 10, 30, 60), timedelta(minutes=5), timedelta(hours=24), timedelta(seconds=30))
    assert spec == (POLL_SCHEDULE, POLL_EVERY, TRACK_ID_WINDOW, SETTLE_DELAY)
    assert timedelta(minutes=2) == RECEIPT_TOLERANCE


def test_polls_follow_the_schedule_then_every_five_minutes() -> None:
    assert [next_poll(polls).total_seconds() for polls in range(8)] == [1, 2, 5, 10, 30, 60, 300, 300]


@given(st.integers(min_value=0, max_value=10_000))
def test_a_poll_is_never_sooner_than_the_one_before(polls: int) -> None:
    assert next_poll(polls + 1) >= next_poll(polls) > timedelta(0)


def test_a_negative_count_of_polls_is_a_defect() -> None:
    with pytest.raises(InvalidValueError, match="polls must be 0 or more"):
        next_poll(-1)


def test_a_track_id_received_three_minutes_before_signing_is_another_document_s() -> None:
    early = TrackId("early", SIGNED_AT - timedelta(minutes=3))
    assert adoptable([early], signed_at=SIGNED_AT, excluded=frozenset()) is None


def test_the_tolerance_s_edge_is_adopted() -> None:
    edge = TrackId("edge", SIGNED_AT - RECEIPT_TOLERANCE)
    assert adoptable([edge], signed_at=SIGNED_AT, excluded=frozenset()) == edge


def test_an_excluded_track_id_is_never_adopted() -> None:
    earlier_document = TrackId("earlier", SIGNED_AT + timedelta(seconds=5))
    assert adoptable([earlier_document], signed_at=SIGNED_AT, excluded=frozenset({"earlier"})) is None


def test_the_newest_of_two_fresh_ones_is_adopted() -> None:
    older = TrackId("older", SIGNED_AT + timedelta(seconds=1))
    newer = TrackId("newer", SIGNED_AT + timedelta(seconds=9))
    stale = TrackId("stale", SIGNED_AT - timedelta(hours=1))
    assert adoptable([older, newer, stale], signed_at=SIGNED_AT, excluded=frozenset()) == newer
    assert adoptable([newer, older], signed_at=SIGNED_AT, excluded=frozenset({"newer"})) == older


def test_a_tie_is_settled_by_the_track_id_whatever_the_order() -> None:
    first = TrackId("a", SIGNED_AT)
    second = TrackId("b", SIGNED_AT)
    assert adoptable([first, second], signed_at=SIGNED_AT, excluded=frozenset()) == second
    assert adoptable([second, first], signed_at=SIGNED_AT, excluded=frozenset()) == second


def test_no_candidates_adopt_nothing() -> None:
    assert adoptable([], signed_at=SIGNED_AT, excluded=frozenset()) is None


def test_moments_carry_their_offset() -> None:
    naive = SIGNED_AT.replace(tzinfo=None)
    with pytest.raises(NaiveMomentError):
        TrackId("x", naive)
    with pytest.raises(NaiveMomentError):
        adoptable([], signed_at=naive, excluded=frozenset())
