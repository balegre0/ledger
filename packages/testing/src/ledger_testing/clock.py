"""Clocks a test moves by hand, for code that takes its clock as an argument."""

from datetime import UTC, datetime
from typing import Final, final
lazy from datetime import timedelta

START: Final = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
"""Where a ``ManualClock`` starts: a fixed instant, so that what a test asserts about times never drifts."""


@final
class Moment:
    """Seconds on a monotonic clock that stands still until a test moves it: ``moment.now += 30``."""

    __slots__ = ("now",)

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        """The time the test set."""
        return self.now


@final
class ManualClock:
    """A wall clock (``Clock``) that stands still until the test moves it."""

    __slots__ = ("now",)

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        """The time the test set."""
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        """Move forward by ``delta``; the new time."""
        self.now += delta
        return self.now
