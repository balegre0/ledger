"""Time, in the three shapes code is handed it so that a test can fix it.

``Clock`` is the wall clock: aware ``datetime`` values in UTC, for what is recorded (an event's time, when a
claim was made). ``MonotonicClock`` is seconds that only move forward, for what is measured (how long a worker
ran, how old a stuck check is): the wall clock can jump. ``Sleep`` waits. Each name means one thing and is
defined here only.
"""

from datetime import UTC, datetime
lazy from collections.abc import Awaitable, Callable

type Clock = Callable[[], datetime]
"""The wall clock: an aware ``datetime`` in UTC (``utc_now``)."""
type MonotonicClock = Callable[[], float]
"""Seconds from an arbitrary start that never go back (``time.monotonic``)."""
type Sleep = Callable[[float], Awaitable[None]]
"""Waits that many seconds (``asyncio.sleep``)."""


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)
