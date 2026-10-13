"""A clock a test moves by hand."""

from typing import final
lazy from datetime import datetime, timedelta


@final
class ManualClock:
    """``clock()`` is ``now`` until ``advance`` moves it."""

    __slots__ = ("now",)

    def __init__(self, now: datetime) -> None:
        self.now: datetime = now

    def __call__(self) -> datetime:
        """The moment the clock shows."""
        return self.now

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward by ``delta``."""
        self.now += delta
