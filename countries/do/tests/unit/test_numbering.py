from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final, final
from uuid import UUID, uuid4
lazy from collections.abc import Iterable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.encf import MAX_SEQUENCE, EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.notation import NaiveMomentError, local_date
from ledger_country_do.channels.ecf_v1.domain.numbering import (
    Allocation,
    NoNumberAvailableError,
    RangeInvalidError,
    RangeState,
    RangeStatus,
    Released,
    accounting_holds,
    choose,
    overlaps,
    release_at,
)
from ledger_country_do.channels.ecf_v1.domain.polling import RECEIPT_TOLERANCE

T31: Final = EcfType.CREDIT_INVOICE
NOW: Final = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
LOW: Final = UUID(int=1)
HIGH: Final = UUID(int=2)


def a_range(first: int, last: int, *, next_: int | None = None, ident: UUID | None = None) -> RangeState:
    """An active range of type 31, expiring at the end of 2028."""
    return RangeState(
        id=ident or uuid4(),
        ecf_type=T31,
        first=first,
        last=last,
        next=first if next_ is None else next_,
        expires_on=date(2028, 12, 31),
        status=RangeStatus.ACTIVE,
    )


def test_the_range_s_next_number_when_nothing_is_released() -> None:
    allocated = choose(ecf_type=T31, ranges=[a_range(1, 10, next_=4, ident=LOW)], released=(), now=NOW)
    assert allocated == Allocation(
        encf=Encf(T31, 4), range_id=LOW, expires_on=date(2028, 12, 31), released=False
    )


def test_the_lowest_range_with_room_first() -> None:
    ranges = [
        a_range(100, 200, ident=HIGH),
        a_range(1, 10, next_=11, ident=UUID(int=3)),
        a_range(50, 60, ident=LOW),
    ]
    assert choose(ecf_type=T31, ranges=ranges, released=(), now=NOW).encf == Encf(T31, 50)


def test_ranges_of_another_type_or_voided_are_never_used() -> None:
    ranges = [
        replace(a_range(1, 10), ecf_type=EcfType.CONSUMER_INVOICE),
        replace(a_range(20, 30), status=RangeStatus.VOIDED),
        a_range(40, 50, ident=LOW),
    ]
    assert choose(ecf_type=T31, ranges=ranges, released=(), now=NOW).encf == Encf(T31, 40)


def test_a_released_number_is_taken_first_once_it_is_available() -> None:
    ranges = [a_range(1, 10, next_=6, ident=LOW)]
    released = [Released(ecf_type=T31, sequence=3, available_at=release_at(NOW))]
    one_minute = choose(ecf_type=T31, ranges=ranges, released=released, now=NOW + timedelta(minutes=1))
    assert one_minute.encf == Encf(T31, 6)
    assert not one_minute.released
    three_minutes = choose(ecf_type=T31, ranges=ranges, released=released, now=NOW + timedelta(minutes=3))
    assert three_minutes == Allocation(
        encf=Encf(T31, 3), range_id=LOW, expires_on=date(2028, 12, 31), released=True
    )


def test_a_released_number_is_available_at_its_moment_exactly() -> None:
    ranges = [a_range(1, 10, next_=6, ident=LOW)]
    released = [Released(ecf_type=T31, sequence=3, available_at=NOW)]
    assert choose(ecf_type=T31, ranges=ranges, released=released, now=NOW).encf == Encf(T31, 3)
    earlier = NOW - timedelta(microseconds=1)
    assert choose(ecf_type=T31, ranges=ranges, released=released, now=earlier).encf == Encf(T31, 6)


def test_a_released_number_the_range_has_not_given_is_never_taken() -> None:
    ranges = [a_range(1, 10, next_=6, ident=LOW)]
    released = [Released(ecf_type=T31, sequence=8, available_at=NOW)]
    assert choose(ecf_type=T31, ranges=ranges, released=released, now=NOW).encf == Encf(T31, 6)


def test_a_released_number_s_moment_carries_its_offset() -> None:
    with pytest.raises(NaiveMomentError):
        Released(ecf_type=T31, sequence=3, available_at=NOW.replace(tzinfo=None))


def test_the_lowest_released_number_first() -> None:
    ranges = [a_range(1, 10, next_=11, ident=LOW)]
    released = [
        Released(ecf_type=T31, sequence=7, available_at=NOW),
        Released(ecf_type=T31, sequence=2, available_at=NOW),
        Released(ecf_type=EcfType.CONSUMER_INVOICE, sequence=1, available_at=NOW),
    ]
    assert choose(ecf_type=T31, ranges=ranges, released=released, now=NOW).encf == Encf(T31, 2)


def test_a_released_number_outside_a_usable_range_is_never_reused() -> None:
    expired = replace(a_range(1, 10, next_=11), expires_on=date(2026, 10, 1))
    released = [
        Released(ecf_type=T31, sequence=4, available_at=NOW),
        Released(ecf_type=T31, sequence=500, available_at=NOW),
    ]
    ranges = [expired, a_range(20, 30, ident=LOW)]
    assert choose(ecf_type=T31, ranges=ranges, released=released, now=NOW).encf == Encf(T31, 20)


def test_release_at_is_the_receipt_tolerance_later() -> None:
    assert release_at(NOW) == NOW + RECEIPT_TOLERANCE


def test_a_range_runs_to_the_end_of_its_last_day_in_santo_domingo() -> None:
    last_day = date(2026, 10, 19)
    ranges = [replace(a_range(1, 10, ident=LOW), expires_on=last_day)]
    late_that_evening = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)  # 22:00 on the 19th in Santo Domingo
    assert choose(ecf_type=T31, ranges=ranges, released=(), now=late_that_evening).encf == Encf(T31, 1)
    the_next_day = datetime(2026, 10, 20, 4, 30, tzinfo=UTC)  # 00:30 on the 20th there
    with pytest.raises(NoNumberAvailableError):
        choose(ecf_type=T31, ranges=ranges, released=(), now=the_next_day)


def test_no_number_is_a_conflict_naming_the_type() -> None:
    with pytest.raises(NoNumberAvailableError) as caught:
        choose(ecf_type=T31, ranges=[a_range(1, 3, next_=4)], released=(), now=NOW)
    assert (caught.value.kind, caught.value.code) == ("conflict", "do_ecf_range_missing")
    assert str(caught.value) == "no e-NCF of type 31 is available: register a DGII-authorized range"


@pytest.mark.parametrize(
    ("first", "last", "next_"),
    [(0, 5, 0), (1, MAX_SEQUENCE + 1, 1), (6, 5, 6), (3, 9, 2), (3, 9, 11)],
    ids=["zero", "beyond-ten-digits", "reversed", "next-before-first", "next-past-last"],
)
def test_a_range_runs_within_ten_digits_and_its_next_within_it(first: int, last: int, next_: int) -> None:
    with pytest.raises(RangeInvalidError) as caught:
        a_range(first, last, next_=next_)
    assert (caught.value.kind, caught.value.code) == ("invalid", "do_ecf_range_invalid")


def test_remaining_counts_the_unallocated_numbers() -> None:
    assert a_range(1, 10, next_=4).remaining == 7
    assert a_range(1, 10, next_=11).remaining == 0


@pytest.mark.parametrize(
    ("new", "expected"),
    [
        ((1, 9), False),
        ((21, 30), False),
        ((1, 10), True),
        ((20, 25), True),
        ((12, 13), True),
        ((5, 40), True),
    ],
)
def test_overlaps(new: tuple[int, int], *, expected: bool) -> None:
    assert overlaps(new, [a_range(10, 20)]) is expected


def test_accounting_holds_when_every_number_has_exactly_one_fate() -> None:
    range_ = a_range(1, 10, next_=6)
    assert accounting_holds(range_, used={1, 4, 500}, burned={2}, released={3}, voided={5})
    assert accounting_holds(a_range(1, 10), used=set(), burned=set(), released=set(), voided=set())


@pytest.mark.parametrize(
    ("used", "burned"),
    [({1, 2, 3, 4}, set[int]()), ({1, 2, 3, 4, 5}, {5}), ({1, 2, 3, 4, 6}, set[int]())],
    ids=["a-gap", "two-fates", "beyond-next"],
)
def test_accounting_fails_on_a_gap_a_double_fate_or_a_number_not_yet_allocated(
    used: set[int], burned: set[int]
) -> None:
    assert not accounting_holds(
        a_range(1, 10, next_=6), used=used, burned=burned, released=set(), voided=set()
    )


T32: Final = EcfType.CONSUMER_INVOICE
EXPIRING: Final = UUID(int=3)
OTHER: Final = UUID(int=4)


class Action(StrEnum):
    """What happens to the series: time passes, a document is numbered, rejected or voided."""

    WAIT = "wait"
    ALLOCATE = "allocate"
    ALLOCATE_OTHER_TYPE = "allocate_other_type"
    REJECT_USED = "reject_used"
    """Rejected with ``secuenciaUtilizada = true``: the number burned."""
    REJECT_RELEASED = "reject_released"
    """Rejected with ``secuenciaUtilizada = false``: the number released."""
    VOID = "void"


def of_type(numbers: Iterable[Encf], ecf_type: EcfType) -> set[int]:
    """The sequences of ``numbers`` of ``ecf_type``."""
    return {number.sequence for number in numbers if number.ecf_type is ecf_type}


@final
class Series:
    """Two types' ranges, one expiring within the run, and the repository's bookkeeping around ``choose``."""

    def __init__(self) -> None:
        self.now: datetime = NOW
        self.ranges: dict[UUID, RangeState] = {
            LOW: a_range(1, 6, ident=LOW),
            HIGH: a_range(10, 14, ident=HIGH),
            EXPIRING: replace(a_range(20, 40, ident=EXPIRING), expires_on=date(2026, 10, 19)),
            OTHER: replace(a_range(1, 8, ident=OTHER), ecf_type=T32),
        }
        self.live: list[Encf] = []
        """The numbers held by documents neither rejected nor voided."""
        self.burned: set[Encf] = set()
        self.released: dict[Encf, datetime] = {}
        self.voided: set[Encf] = set()

    def act(self, action: Action, value: int) -> None:
        match action:
            case Action.WAIT:
                self.now += timedelta(hours=value)
            case Action.ALLOCATE:
                self.allocate(T31)
            case Action.ALLOCATE_OTHER_TYPE:
                self.allocate(T32)
            case _ if not self.live:
                pass
            case Action.REJECT_USED:
                self.burned.add(self.live.pop(value % len(self.live)))
            case Action.REJECT_RELEASED:
                self.released[self.live.pop(value % len(self.live))] = release_at(self.now)
            case _:
                self.voided.add(self.live.pop(value % len(self.live)))

    def allocate(self, ecf_type: EcfType) -> None:
        pool = [
            Released(ecf_type=number.ecf_type, sequence=number.sequence, available_at=at)
            for number, at in self.released.items()
        ]
        today = local_date(self.now)
        usable = [
            each for each in self.ranges.values() if each.ecf_type is ecf_type and each.usable_on(today)
        ]
        try:
            allocated = choose(
                ecf_type=ecf_type, ranges=list(self.ranges.values()), released=pool, now=self.now
            )
        except NoNumberAvailableError:
            assert all(each.remaining == 0 for each in usable)
            assert not any(
                number.ecf_type is ecf_type
                and at <= self.now
                and any(each.holds(number.sequence) for each in usable)
                for number, at in self.released.items()
            )
            return
        number = allocated.encf
        range_ = self.ranges[allocated.range_id]
        assert number.ecf_type is ecf_type
        assert range_ in usable
        assert number not in self.live
        if allocated.released:
            assert self.released.pop(number) <= self.now
        else:
            assert number.sequence == range_.next
            self.ranges[allocated.range_id] = replace(range_, next=number.sequence + 1)
        self.live.append(number)

    def check(self) -> None:
        assert len(self.live) == len(set(self.live))
        for range_ in self.ranges.values():
            assert accounting_holds(
                range_,
                used=of_type(self.live, range_.ecf_type),
                burned=of_type(self.burned, range_.ecf_type),
                released=of_type(self.released, range_.ecf_type),
                voided=of_type(self.voided, range_.ecf_type),
            )


@settings(max_examples=300)
@given(st.lists(st.tuples(st.sampled_from(list(Action)), st.integers(min_value=0, max_value=4)), max_size=80))
def test_numbering_never_gives_a_number_twice_nor_loses_one(actions: list[tuple[Action, int]]) -> None:
    series = Series()
    for action, value in actions:
        series.act(action, value)
        series.check()
