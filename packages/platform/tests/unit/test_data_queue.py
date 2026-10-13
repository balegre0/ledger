from datetime import timedelta

import pytest

from ledger_platform.data.queue import DEFAULT_BACKOFF, Backoff


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [
        (1, timedelta(seconds=1)),
        (2, timedelta(seconds=10)),
        (3, timedelta(minutes=1)),
        (4, timedelta(minutes=5)),
        (5, timedelta(minutes=10)),
        (40, timedelta(minutes=10)),
    ],
)
def test_the_default_backoff_is_the_specs(attempts: int, delay: timedelta) -> None:
    assert DEFAULT_BACKOFF.after(attempts) == delay


def test_attempts_below_one_wait_the_longest() -> None:
    """Attempts count from 1 on claim: a count below it is a defect, and the safe wait is the long one."""
    assert DEFAULT_BACKOFF.after(0) == timedelta(minutes=10)


def test_a_backoff_without_steps_always_waits_then() -> None:
    assert Backoff(steps=(), then=timedelta(seconds=3)).after(1) == timedelta(seconds=3)
