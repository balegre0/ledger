"""When an accepted e-CF's delivery to its buyer is tried again, and until when (spec §8.6.5; Decision 21).

A buyer that could not be reached is tried again after ``DELIVERY_SCHEDULE`` (1 minute, 5 minutes, 30
minutes, 2 hours), then every ``DELIVERY_EVERY`` (6 hours), until ``DELIVERY_WINDOW`` (7 days) from the moment
the delivery started (the first run after DGII accepted the document, or the client's retry): then it failed.
No wait reaches past the window's end (``next_delivery``), so the run that records the failure always comes.
"""

from datetime import timedelta
from typing import Final
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.domain.notation import require_aware
from ledger_kernel.errors import InvalidValueError

DELIVERY_SCHEDULE: Final = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
"""The waits after each of the first failed attempts."""
DELIVERY_EVERY: Final = timedelta(hours=6)
"""Between attempts once the schedule is spent."""
DELIVERY_WINDOW: Final = timedelta(days=7)
"""How long a delivery is tried from its start before it failed for good (a client may retry it)."""


def delivery_wait(attempts: int) -> timedelta:
    """The wait before the next attempt, ``attempts`` (one or more) having failed."""
    if attempts < 1:
        raise InvalidValueError(field="delivery_attempts", requirement="1 or more")
    return DELIVERY_SCHEDULE[attempts - 1] if attempts <= len(DELIVERY_SCHEDULE) else DELIVERY_EVERY


def delivery_open(since: datetime, now: datetime) -> bool:
    """Whether a delivery started at ``since`` is still tried at ``now``."""
    return require_aware(now) - require_aware(since) < DELIVERY_WINDOW


def next_delivery(since: datetime, now: datetime, attempts: int) -> datetime:
    """When to try again at ``now``, ``attempts`` having failed: never past the window's end."""
    return min(require_aware(now) + delivery_wait(attempts), require_aware(since) + DELIVERY_WINDOW)
