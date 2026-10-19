import asyncio
from datetime import timedelta
from typing import final

import pytest

from ledger_platform.data.queue import DEFAULT_BACKOFF, Backoff, renewing


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


class RenewalLostError(Exception):
    """What the tests' ``renewing`` raises when the lease is gone."""

    def __init__(self) -> None:
        super().__init__("the test's lease is gone")


class RenewalBrokeError(Exception):
    """A renewal that fails (the database)."""

    def __init__(self) -> None:
        super().__init__("the test's renewal broke")


@final
class Renewals:
    """A fake ``renew``: answers in order (an exception is raised), then ``True``; counts its calls."""

    __slots__ = ("answers", "calls", "thrice")

    def __init__(self, *answers: bool | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.thrice = asyncio.Event()

    async def __call__(self) -> bool:
        self.calls += 1
        if self.calls >= 3:
            self.thrice.set()
        answer = self.answers.pop(0) if self.answers else True
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_renewing_answers_the_work_and_renews_while_it_runs() -> None:
    renewals = Renewals()

    async def work() -> str:
        await renewals.thrice.wait()
        return "done"

    assert await renewing(work(), renew=renewals, every=0, lost=RenewalLostError) == "done"
    assert renewals.calls >= 3


@pytest.mark.parametrize("answer", [False, RenewalBrokeError()], ids=["taken_over", "failed"])
async def test_a_renewal_that_finds_no_lease_or_fails_stops_the_work(*, answer: bool | Exception) -> None:
    stopped = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    with pytest.raises(RenewalLostError):
        async with asyncio.timeout(5):
            await renewing(work(), renew=Renewals(answer), every=0, lost=RenewalLostError)
    assert stopped.is_set()


async def test_a_cancelled_caller_gets_its_cancellation_even_when_the_lease_was_lost() -> None:
    """The caller's own cancellation is never turned into a lost lease."""
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.Event().wait()

    async def lost_while_cancelled() -> bool:
        caller.cancel()
        return False

    caller = asyncio.create_task(renewing(work(), renew=lost_while_cancelled, every=0, lost=RenewalLostError))
    await started.wait()
    with pytest.raises(asyncio.CancelledError):
        await caller
