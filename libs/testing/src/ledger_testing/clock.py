"""A clock a test moves by hand, for code that takes its clock as an argument."""

from typing import final


@final
class Moment:
    """Seconds on a monotonic clock that stands still until a test moves it: ``moment.now += 30``."""

    __slots__ = ("now",)

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        """The time the test set."""
        return self.now
