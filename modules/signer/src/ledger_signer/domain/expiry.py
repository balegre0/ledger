"""When a company is told its certificate expires (plan Decision 10): notices planned at installation.

Each of the configured days (``LEDGER_SIGNER_EXPIRY_NOTICE_DAYS``, ``30,14,7,1,0`` by default) is a notice at
``not_after - days``: the company is told the certificate expires within that many days (``0``: it expires
now). A notice whose moment passed before the installation is skipped, except one: a certificate installed
inside a window (fewer days left than a notice's) is told that window's notice at once, so a company that
installs a certificate expiring in ten days hears of it now, not in three. Planned once, at installation: a
certificate retired meanwhile is told nothing (the job finds it retired).
"""

from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_kernel.errors import InvalidValueError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Notice:
    """Tell the company, ``at`` (UTC), that its certificate expires within ``threshold_days`` days."""

    threshold_days: int
    at: datetime


def notices(not_after: datetime, *, days: Sequence[int], now: datetime) -> tuple[Notice, ...]:
    """The notices of a certificate expiring at ``not_after``, installed ``now``, by moment (see the module).

    Every moment is in UTC, whatever the zone of the ones given.

    Each of ``days`` (distinct, none negative: a defect otherwise) whose moment is not past; and the notice of
    the window it is installed in (the fewest days whose moment is not ahead), at ``now``, when that moment
    has passed. None for a certificate that has expired.
    """
    if not_after.utcoffset() is None or now.utcoffset() is None:
        raise InvalidValueError(field="notices", requirement="aware datetimes")
    not_after, now = not_after.astimezone(UTC), now.astimezone(UTC)
    if len(frozenset(days)) != len(days) or any(day < 0 for day in days):
        raise InvalidValueError(field="notices.days", requirement="distinct days, none negative")
    if not_after <= now:
        return ()
    planned = [Notice(threshold_days=day, at=not_after - timedelta(days=day)) for day in days]
    upcoming = [notice for notice in planned if notice.at >= now]
    reached = [notice for notice in planned if notice.at <= now]
    if reached:  # the window it is installed in: its notice is now, unless it falls due now anyway
        window = min(reached, key=lambda notice: notice.threshold_days)
        if window.at < now:
            upcoming.append(Notice(threshold_days=window.threshold_days, at=now))
    return tuple(sorted(upcoming, key=lambda notice: notice.at))
