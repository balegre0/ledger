"""Time: always aware, always UTC. Code takes a ``Clock`` so tests can fix the time."""

from datetime import UTC, datetime
lazy from collections.abc import Callable

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)
