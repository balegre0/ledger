from datetime import timedelta

from ledger_testing.clock import START, ManualClock, Moment


def test_a_moment_stands_still_until_the_test_moves_it() -> None:
    moment = Moment(1_000.0)
    assert (moment(), moment()) == (1_000.0, 1_000.0)
    moment.now += 30
    assert moment() == 1_030.0
    assert Moment()() == 0.0


def test_a_manual_clock_stands_still_until_moved() -> None:
    clock = ManualClock()
    assert clock() == START == clock()
    assert clock.advance(timedelta(seconds=90)) == START + timedelta(seconds=90) == clock()


def test_the_plugins_clock_starts_at_the_start(clock: ManualClock) -> None:
    assert clock() == START
