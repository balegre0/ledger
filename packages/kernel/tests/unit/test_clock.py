import asyncio
from datetime import UTC, datetime
from time import monotonic
from typing import Final

from ledger_kernel.clock import utc_now
lazy from ledger_kernel.clock import Clock, MonotonicClock, Sleep

WALL: Final[Clock] = utc_now
"""The wall clock is a ``Clock``: aware datetimes, for what is recorded."""
ELAPSED: Final[MonotonicClock] = monotonic
"""``time.monotonic`` is a ``MonotonicClock``: seconds that only move forward, for what is measured."""
WAIT: Final[Sleep] = asyncio.sleep
"""``asyncio.sleep`` is a ``Sleep``."""


def test_utc_now_is_aware_and_in_utc() -> None:
    assert utc_now().tzinfo is UTC


def test_utc_now_reads_the_current_time() -> None:
    before = datetime.now(UTC)
    instant = utc_now()
    after = datetime.now(UTC)
    assert before <= instant <= after


def test_each_kind_of_time_has_a_name_of_its_own() -> None:
    """A wall clock and a monotonic one are not interchangeable: the names say which is which."""
    assert isinstance(WALL(), datetime)
    assert isinstance(ELAPSED(), float)
    assert WAIT is asyncio.sleep
