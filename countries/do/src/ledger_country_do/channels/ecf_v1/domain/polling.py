"""When DGII is asked again, and which track id settles an unknown outcome (spec §8.6.4; Decisions 7-9).

A result is polled after ``POLL_SCHEDULE`` (1, 2, 5, 10, 30 and 60 seconds), then every ``POLL_EVERY``. A
submission whose answer was lost waits ``SETTLE_DELAY`` (at least the longest attempt at a DGII call, so a
request still in flight has landed) before the e-NCF's track ids are asked for, and is asked about for
``TRACK_ID_WINDOW`` before it is left to a person.

A track id is adopted only when DGII received it no earlier than ``RECEIPT_TOLERANCE`` before the moment of
signing (an older one is another, earlier document: clocks differ by less) and it is not one recorded for an
earlier document with the same e-NCF (a released number, Decision 9). The document is never sent again.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.domain.notation import local_date
from ledger_kernel.errors import InvalidValueError

POLL_SCHEDULE: Final = (1, 2, 5, 10, 30, 60)
"""Seconds before each of the first polls of a result."""
POLL_EVERY: Final = timedelta(minutes=5)
"""Between polls once the schedule is spent."""
TRACK_ID_WINDOW: Final = timedelta(hours=24)
"""How long an unknown outcome is asked about before it is parked for a person (Decision 7)."""
SETTLE_DELAY: Final = timedelta(seconds=30)
"""Waited after an unknown outcome before DGII is asked: at least the attempt limit of a call (20 s)."""
RECEIPT_TOLERANCE: Final = timedelta(minutes=2)
"""How long before the moment of signing DGII's reception time of our document may read (clocks differ)."""


def next_poll(polls: int) -> timedelta:
    """The wait before the next poll, when ``polls`` were made already."""
    if polls < 0:
        raise InvalidValueError(field="polls", requirement="0 or more")
    if polls < len(POLL_SCHEDULE):
        return timedelta(seconds=POLL_SCHEDULE[polls])
    return POLL_EVERY


@final
@dataclass(frozen=True, slots=True)
class TrackId:
    """A track id DGII gave an e-NCF, and when DGII received the document."""

    track_id: str
    received_at: datetime

    def __post_init__(self) -> None:
        local_date(self.received_at)  # NaiveMomentError without a UTC offset


def adoptable(
    candidates: Sequence[TrackId], *, signed_at: datetime, excluded: frozenset[str]
) -> TrackId | None:
    """The newest of ``candidates`` received from ``RECEIPT_TOLERANCE`` before ``signed_at`` and not excluded.

    ``None`` when there is none. Of two received at the same moment, the greater id, whatever their order.
    """
    local_date(signed_at)  # NaiveMomentError without a UTC offset
    since = signed_at - RECEIPT_TOLERANCE
    fresh = [each for each in candidates if each.received_at >= since and each.track_id not in excluded]
    return max(fresh, key=lambda each: (each.received_at, each.track_id), default=None)
