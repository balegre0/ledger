import asyncio
import contextlib
import gc
import weakref
lazy from collections.abc import Callable

import pytest
from structlog.testing import capture_logs

from ledger_platform.health import DuplicateCheckError, Health


async def healthy() -> None:
    return None


async def broken() -> None:
    raise ConnectionError


async def stuck() -> None:
    await asyncio.sleep(10)


async def test_every_check_is_reported() -> None:
    health = Health(timeout=0.05)
    health.add("database", healthy)
    health.add("nats", broken)
    health.add("slow", stuck)
    assert await health.run() == {"database": True, "nats": False, "slow": False}


async def test_checks_run_concurrently() -> None:
    """Each check waits for the other to have started: one after the other, the first would time out."""
    first, second = asyncio.Event(), asyncio.Event()

    async def meets_second() -> None:
        first.set()
        await second.wait()

    async def meets_first() -> None:
        second.set()
        await first.wait()

    health = Health(timeout=5)
    health.add("first", meets_second)
    health.add("second", meets_first)
    assert await health.run() == {"first": True, "second": True}


async def test_a_check_that_swallows_its_cancellation_is_unhealthy() -> None:
    """The deadline cancels the check; one that carries on as if nothing happened has still missed it."""

    async def gives_up_quietly() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(10)

    health = Health(timeout=0.05)
    health.add("quiet", gives_up_quietly)
    assert await health.run() == {"quiet": False}


async def test_a_check_that_raises_cancelled_by_itself_is_unhealthy() -> None:
    async def cancels_itself() -> None:
        raise asyncio.CancelledError

    health = Health()
    health.add("odd", cancels_itself)
    assert await health.run() == {"odd": False}


async def test_a_check_that_ignores_cancellation_does_not_hold_up_the_probe() -> None:
    release, finished = asyncio.Event(), asyncio.Event()
    started: list[str] = []

    async def stubborn() -> None:
        started.append("stubborn")
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()
        finished.set()

    health = Health(timeout=0.05)
    health.add("stubborn", stubborn)
    health.add("database", healthy)
    try:
        async with asyncio.timeout(2):
            first = await health.run()
            second = await health.run()
        assert first == second == {"stubborn": False, "database": True}
        assert started == ["stubborn"], "a check still running is not started again"
    finally:
        release.set()
    await finished.wait()
    await asyncio.sleep(0)
    assert await health.run() == {"stubborn": True, "database": True}
    assert started == ["stubborn", "stubborn"]


async def test_cancelling_a_run_propagates_and_the_check_stops_at_its_own_deadline() -> None:
    running, cancelled = asyncio.Event(), asyncio.Event()

    async def waits() -> None:
        running.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    health = Health(timeout=0.05)
    health.add("slow", waits)
    probe = asyncio.create_task(health.run())
    await running.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    async with asyncio.timeout(2):
        await cancelled.wait()
    await asyncio.sleep(0)


async def test_a_prober_that_gives_up_does_not_cancel_the_check_for_the_others() -> None:
    running, release = asyncio.Event(), asyncio.Event()
    cancelled = False

    async def slow() -> None:
        nonlocal cancelled
        running.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    health = Health(timeout=5)
    health.add("slow", slow)
    leaving = asyncio.create_task(health.run())
    staying = asyncio.create_task(health.run())
    await running.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    release.set()
    async with asyncio.timeout(2):
        assert await staying == {"slow": True}
    assert not cancelled


def record_into(
    problems: list[dict[str, object]],
) -> Callable[[asyncio.AbstractEventLoop, dict[str, object]], None]:
    """An exception handler for the loop: what a callback or a task raised and nobody awaited lands here."""

    def record(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        del loop
        problems.append(context)

    return record


async def test_overlapping_runs_put_one_probe_of_a_check_in_flight_and_no_callback_fails() -> None:
    """Two probes at once (kubelet and a load balancer) while a check hangs: one probe, nothing raised."""
    release, ended = asyncio.Event(), asyncio.Event()
    running = peak = 0
    problems: list[dict[str, object]] = []

    async def stubborn() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            while not release.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await release.wait()
        finally:
            running -= 1
            ended.set()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(record_into(problems))
    health = Health(timeout=0.05)
    health.add("stubborn", stubborn)
    try:
        async with asyncio.timeout(3):
            overlapping = await asyncio.gather(health.run(), health.run())
            later = await health.run()
    finally:
        release.set()
    await ended.wait()
    await asyncio.sleep(0)
    loop.set_exception_handler(None)
    first, second = overlapping
    assert first == second == {"stubborn": False}
    assert later == {"stubborn": False}
    assert peak == 1
    assert problems == []


async def test_a_stuck_check_is_cancelled_again_by_every_later_run() -> None:
    release, ended = asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def stubborn() -> None:
        nonlocal cancellations
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations += 1
        ended.set()

    health = Health(timeout=0.02)
    health.add("stubborn", stubborn)
    seen: list[int] = []
    try:
        async with asyncio.timeout(3):
            for _ in range(3):
                await health.run()
                await asyncio.sleep(0)
                seen.append(cancellations)
    finally:
        release.set()
    await ended.wait()
    assert seen == [1, 2, 3]


async def test_a_check_stuck_past_the_limit_is_wedged_until_it_ends() -> None:
    release, ended = asyncio.Event(), asyncio.Event()
    now = [100.0]

    async def stubborn() -> None:
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()
        ended.set()

    health = Health(timeout=0.02, wedge_after=30, clock=lambda: now[0])
    health.add("stubborn", stubborn)
    health.add("database", healthy)
    try:
        await health.run()
        assert health.wedged() == ()
        now[0] += 30
        assert health.wedged() == ()
        now[0] += 0.5
        assert health.wedged() == ("stubborn",)
    finally:
        release.set()
    await ended.wait()
    await asyncio.sleep(0)
    assert health.wedged() == ()
    assert await health.run() == {"stubborn": True, "database": True}


async def test_the_limit_for_a_wedge_is_ten_timeouts_unless_chosen() -> None:
    release = asyncio.Event()
    now = [0.0]

    async def stubborn() -> None:
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()

    health = Health(timeout=0.01, clock=lambda: now[0])
    health.add("stubborn", stubborn)
    try:
        await health.run()
        now[0] = 0.09
        assert health.wedged() == ()
        now[0] = 0.11
        assert health.wedged() == ("stubborn",)
    finally:
        release.set()
        await asyncio.sleep(0)


class LateError(Exception):
    pass


@pytest.mark.parametrize("noticed_by", ["a run", "a liveness check"])
async def test_a_stuck_check_that_ended_is_forgotten_with_its_traceback(noticed_by: str) -> None:
    release, ended = asyncio.Event(), asyncio.Event()
    errors: list[weakref.ref[LateError]] = []

    async def fails_when_released() -> None:
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()
        error = LateError()
        errors.append(weakref.ref(error))
        ended.set()
        raise error

    health = Health(timeout=0.02)
    health.add("stubborn", fails_when_released)
    try:
        await health.run()
    finally:
        release.set()
    await ended.wait()
    await asyncio.sleep(0)
    (late,) = errors
    gc.collect()
    assert late() is not None, "kept until a probe or a liveness check notices that it ended"
    if noticed_by == "a run":
        await health.run()
    else:
        assert health.wedged() == ()
    gc.collect()
    assert late() is None


async def test_no_checks_is_healthy() -> None:
    assert await Health().run() == {}


def test_a_name_is_used_once() -> None:
    health = Health()
    health.add("database", healthy)
    with pytest.raises(DuplicateCheckError, match="database"):
        health.add("database", healthy)


def test_a_part_of_the_process_that_is_gone_is_named_dead_while_it_stays_gone() -> None:
    gone: list[str] = []
    health = Health()
    health.add_liveness("workers", lambda: not gone)
    health.add_liveness("loop", lambda: True)
    assert health.dead() == ()
    gone.append("consumer")
    assert health.dead() == ("workers",)


def test_a_liveness_condition_is_no_readiness_check() -> None:
    health = Health()
    health.add_liveness("workers", lambda: False)
    assert health.wedged() == ()


async def test_a_readiness_check_is_no_liveness_condition() -> None:
    health = Health()
    health.add("nats", broken)
    assert await health.run() == {"nats": False}
    assert health.dead() == ()


def test_a_liveness_name_is_used_once() -> None:
    health = Health()
    health.add_liveness("workers", lambda: True)
    with pytest.raises(DuplicateCheckError, match="workers"):
        health.add_liveness("workers", lambda: True)


async def test_a_failing_check_is_logged_in_full_when_it_starts_failing_and_briefly_while_it_stays_so() -> (
    None
):
    """An outage is probed every few seconds by every prober: one traceback says why, not one per probe."""
    failing = [True]

    async def database() -> None:
        if failing[0]:
            raise ConnectionError

    health = Health()
    health.add("database", database)
    with capture_logs() as logs:
        for _ in range(3):
            await health.run()
        failing[0] = False
        await health.run()
        await health.run()
        failing[0] = True
        await health.run()
    first, *during, recovered, again = logs
    assert isinstance(first.pop("exc_info"), ConnectionError)
    assert first == {"event": "health.check_failed", "log_level": "error", "check": "database"}
    assert during == [{"event": "health.check_failed", "log_level": "debug", "check": "database"}] * 2
    assert recovered == {"event": "health.check_recovered", "log_level": "info", "check": "database"}
    assert isinstance(again.pop("exc_info"), ConnectionError)


async def test_a_check_that_hangs_is_logged_once_as_timed_out_and_then_only_in_passing() -> None:
    release = asyncio.Event()

    async def hangs() -> None:
        await release.wait()

    health = Health(timeout=0.02)
    health.add("nats", hangs)
    try:
        with capture_logs() as logs:
            for _ in range(3):
                await health.run()
    finally:
        release.set()
        await asyncio.sleep(0)
    assert [(entry["event"], entry["log_level"]) for entry in logs] == [
        ("health.check_timed_out", "error"),
        ("health.check_timed_out", "debug"),
        ("health.check_timed_out", "debug"),
    ]


async def test_a_check_that_cancels_itself_is_logged_once_and_then_only_in_passing() -> None:
    async def cancels_itself() -> None:
        raise asyncio.CancelledError

    health = Health()
    health.add("odd", cancels_itself)
    with capture_logs() as logs:
        await health.run()
        await health.run()
    assert [(entry["event"], entry["log_level"]) for entry in logs] == [
        ("health.check_cancelled", "error"),
        ("health.check_cancelled", "debug"),
    ]
