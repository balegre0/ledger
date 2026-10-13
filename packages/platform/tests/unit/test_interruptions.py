from typing import Final

from structlog.testing import capture_logs

from ledger_platform.interruptions import Interruptions

BROKEN: Final = "listener broke"


def test_every_listener_is_told_of_every_interruption() -> None:
    interruptions = Interruptions()
    heard: list[str] = []
    _ = interruptions.listen(lambda: heard.append("first"))
    _ = interruptions.listen(lambda: heard.append("second"))
    interruptions.interrupted()
    interruptions.interrupted()
    assert heard == ["first", "second", "first", "second"]


def test_a_listener_that_stops_listening_is_told_nothing_more() -> None:
    interruptions = Interruptions()
    heard: list[str] = []
    stop = interruptions.listen(lambda: heard.append("gone"))
    _ = interruptions.listen(lambda: heard.append("kept"))
    interruptions.interrupted()
    stop()
    stop()  # stopping twice is stopping
    interruptions.interrupted()
    assert heard == ["gone", "kept", "kept"]


def test_the_same_listener_twice_is_told_twice_and_stopped_one_at_a_time() -> None:
    interruptions = Interruptions()
    heard: list[str] = []

    def listener() -> None:
        heard.append("heard")

    stop = interruptions.listen(listener)
    _ = interruptions.listen(listener)
    interruptions.interrupted()
    stop()
    interruptions.interrupted()
    assert len(heard) == 3


def test_a_listener_that_fails_is_logged_and_the_others_are_still_told() -> None:
    interruptions = Interruptions()
    heard: list[str] = []

    def broken() -> None:
        raise RuntimeError(BROKEN)

    _ = interruptions.listen(broken)
    _ = interruptions.listen(lambda: heard.append("told"))
    with capture_logs() as logs:
        interruptions.interrupted()
    assert heard == ["told"]
    assert [(log["event"], log["log_level"], log["error"]) for log in logs] == [
        ("nats.interruption_listener_failed", "error", "RuntimeError")
    ]


def test_a_listener_may_stop_listening_while_it_is_told() -> None:
    interruptions = Interruptions()
    heard: list[str] = []

    def once() -> None:
        heard.append("once")
        stop()

    stop = interruptions.listen(once)
    _ = interruptions.listen(lambda: heard.append("always"))
    interruptions.interrupted()
    interruptions.interrupted()
    assert heard == ["once", "always", "always"]
