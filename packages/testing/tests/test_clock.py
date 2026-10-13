from ledger_testing.clock import Moment


def test_a_moment_stands_still_until_the_test_moves_it() -> None:
    moment = Moment(1_000.0)
    assert (moment(), moment()) == (1_000.0, 1_000.0)
    moment.now += 30
    assert moment() == 1_030.0
    assert Moment()() == 0.0
