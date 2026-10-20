"""Expiry notices planned at installation (plan Decision 10).

The configured days; the window a certificate is installed in, told at once; nothing past; nothing for an
expired certificate.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_kernel.errors import InvalidValueError
from ledger_signer.domain.expiry import Notice, notices

NOT_AFTER: Final = datetime(2027, 10, 19, 12, tzinfo=UTC)
DAYS: Final = (30, 14, 7, 1, 0)


def days_before(days: float) -> datetime:
    return NOT_AFTER - timedelta(days=days)


def test_a_certificate_installed_long_before_it_expires_gets_every_notice() -> None:
    assert notices(NOT_AFTER, days=DAYS, now=days_before(365)) == tuple(
        Notice(threshold_days=day, at=days_before(day)) for day in DAYS
    )


def test_a_certificate_installed_inside_a_window_is_told_that_window_at_once() -> None:
    now = days_before(10)
    assert notices(NOT_AFTER, days=DAYS, now=now) == (
        Notice(threshold_days=14, at=now),
        Notice(threshold_days=7, at=days_before(7)),
        Notice(threshold_days=1, at=days_before(1)),
        Notice(threshold_days=0, at=NOT_AFTER),
    )


def test_a_notice_due_at_the_installation_is_the_window_s() -> None:
    now = days_before(7)
    assert notices(NOT_AFTER, days=DAYS, now=now) == (
        Notice(threshold_days=7, at=now),
        Notice(threshold_days=1, at=days_before(1)),
        Notice(threshold_days=0, at=NOT_AFTER),
    )


def test_the_last_day_is_told_at_once_then_the_expiry_itself() -> None:
    now = days_before(0.5)
    assert notices(NOT_AFTER, days=DAYS, now=now) == (
        Notice(threshold_days=1, at=now),
        Notice(threshold_days=0, at=NOT_AFTER),
    )


def test_zero_is_the_moment_it_expires_and_the_order_of_days_does_not_matter() -> None:
    assert notices(NOT_AFTER, days=(0, 30), now=days_before(365)) == (
        Notice(threshold_days=30, at=days_before(30)),
        Notice(threshold_days=0, at=NOT_AFTER),
    )
    assert notices(NOT_AFTER, days=(), now=days_before(365)) == ()


def test_notices_are_in_utc_whatever_zone_they_were_asked_in() -> None:
    santo_domingo = timezone(timedelta(hours=-4))
    planned = notices(
        NOT_AFTER.astimezone(santo_domingo), days=(7,), now=days_before(10).astimezone(santo_domingo)
    )
    assert planned == (Notice(threshold_days=7, at=days_before(7)),)
    assert all(notice.at.tzinfo is UTC for notice in planned)


def test_an_expired_certificate_is_told_nothing() -> None:
    assert notices(NOT_AFTER, days=DAYS, now=NOT_AFTER) == ()
    assert notices(NOT_AFTER, days=DAYS, now=NOT_AFTER + timedelta(days=1)) == ()


@pytest.mark.parametrize("days", [(7, 7), (7, -1)])
def test_days_our_settings_could_not_have_given_are_a_defect(days: tuple[int, ...]) -> None:
    with pytest.raises(InvalidValueError):
        notices(NOT_AFTER, days=days, now=days_before(365))


@pytest.mark.parametrize(
    ("not_after", "now"),
    [(NOT_AFTER.replace(tzinfo=None), days_before(1)), (NOT_AFTER, days_before(1).replace(tzinfo=None))],
)
def test_naive_moments_are_a_defect(not_after: datetime, now: datetime) -> None:
    with pytest.raises(InvalidValueError):
        notices(not_after, days=DAYS, now=now)


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])  # a busy machine draws slowly
@given(
    days=st.lists(st.integers(min_value=0, max_value=366), unique=True, max_size=8),
    left=st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=400)),
)
def test_notices_are_never_past_and_each_upcoming_day_is_told_at_its_moment(
    days: list[int], left: timedelta
) -> None:
    now = NOT_AFTER - left
    planned = notices(NOT_AFTER, days=days, now=now)
    assert [notice.at for notice in planned] == sorted(notice.at for notice in planned)
    assert all(now <= notice.at <= NOT_AFTER for notice in planned)
    assert len({notice.threshold_days for notice in planned}) == len(planned)
    on_time = {notice.threshold_days for notice in planned if notice.at == days_before(notice.threshold_days)}
    assert on_time == {day for day in days if days_before(day) >= now}
    assert all(notice.threshold_days in days for notice in planned)
    caught_up = [notice for notice in planned if notice.at != days_before(notice.threshold_days)]
    reached = [day for day in days if days_before(day) < now]
    if reached and not any(days_before(day) == now for day in days):
        assert caught_up == [Notice(threshold_days=min(reached), at=now)]
    else:
        assert caught_up == []
