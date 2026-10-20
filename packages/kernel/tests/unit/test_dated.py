from datetime import date

import pytest

from ledger_kernel.dated import InvalidTimelineError, NotInEffectError, Since, Timeline


def test_the_newest_value_in_effect_on_a_day_wins() -> None:
    rates = Timeline(Since(date(2026, 1, 1), 18), Since(date(2013, 1, 1), 16))
    assert rates.at(date(2026, 1, 1)) == 18
    assert rates.at(date(2025, 12, 31)) == 16


def test_the_entry_in_effect_on_a_day_names_when_its_value_took_effect() -> None:
    newest, oldest = Since(date(2026, 1, 1), 18), Since(date(2013, 1, 1), 16, until=date(2025, 6, 1))
    rates = Timeline(newest, oldest)
    assert (rates.entry_at(date(2026, 3, 1)), rates.entry_at(date(2013, 1, 1))) == (newest, oldest)
    for day in (date(2025, 6, 1), date(2012, 12, 31)):
        with pytest.raises(NotInEffectError):
            rates.entry_at(day)


def test_a_timeline_lists_its_values_newest_first() -> None:
    newest, oldest = Since(date(2026, 1, 1), 18), Since(date(2013, 1, 1), 16)
    assert Timeline(newest, oldest).entries == (newest, oldest)


def test_a_timeline_is_a_value() -> None:
    timeline = Timeline(Since(date(2026, 1, 1), 18))
    assert timeline == Timeline(Since(date(2026, 1, 1), 18))
    assert hash(timeline) == hash(Timeline(Since(date(2026, 1, 1), 18)))
    assert timeline != Timeline(Since(date(2026, 1, 1), 16))
    assert (
        repr(timeline) == "Timeline(entries=(Since(since=datetime.date(2026, 1, 1), value=18, until=None),))"
    )


def test_before_the_oldest_value_nothing_is_in_effect() -> None:
    with pytest.raises(NotInEffectError) as caught:
        Timeline(Since(date(2013, 1, 1), 16)).at(date(2012, 12, 31))
    assert str(caught.value) == "no value is in effect on 2012-12-31"


def test_a_value_that_ended_is_not_in_effect_after_its_end() -> None:
    abolished = Timeline(Since(date(2020, 1, 1), 5, until=date(2021, 1, 1)))
    assert abolished.at(date(2020, 12, 31)) == 5
    with pytest.raises(NotInEffectError):
        abolished.at(date(2021, 1, 1))


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ((), "holds no value"),
        ((Since(date(2013, 1, 1), 1), Since(date(2026, 1, 1), 2)), "is not newest first"),
        ((Since(date(2026, 1, 1), 1), Since(date(2026, 1, 1), 2)), "is not newest first"),
        (
            (Since(date(2026, 1, 1), 1), Since(date(2013, 1, 1), 2, until=date(2027, 1, 1))),
            "has overlapping values",
        ),
        ((Since(date(2026, 1, 1), 1, until=date(2026, 1, 1)),), "has a value that ends before it starts"),
    ],
)
def test_a_timeline_that_contradicts_itself_is_refused(entries: tuple[Since[int], ...], problem: str) -> None:
    with pytest.raises(InvalidTimelineError, match=rf"^a timeline {problem}$"):
        Timeline(*entries)
