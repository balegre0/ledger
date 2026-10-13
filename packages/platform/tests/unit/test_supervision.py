import asyncio
import contextlib
from typing import ClassVar, Final, Never, final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nats.js.errors import ServerError, ServiceUnavailableError
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs

from ledger_kernel.errors import LedgerError
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.consumer import ConsumerRefusedError
from ledger_platform.runtime.supervision import (
    FIRST_RESTART_DELAY,
    HEALTHY_RUN,
    MAX_RESTART_DELAY,
    RESTART_JITTER,
    Supervisor,
    restart_delay,
)
from ledger_testing.clock import Moment

NAME: Final = "consumer payments-ledger"
NO_JITTER: Final = 0.0
"""The draw that shortens nothing: the delay is exactly the backoff's."""


class MisconfiguredError(LedgerError):
    """What no restart can fix."""

    code: ClassVar[str] = "misconfigured"


class BlipError(ConnectionError):
    """What a restart may well fix."""


@final
class Pause:
    """A sleep that returns at once and moves ``clock`` instead, keeping each delay it was asked for."""

    def __init__(self, clock: Moment) -> None:
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self._clock.now += seconds
        await asyncio.sleep(0)


@final
class Flaky:
    """A worker whose runs end as told, one per run: after ``ran_for`` seconds with ``error``, or never."""

    def __init__(self, clock: Moment, *runs: tuple[float, Exception]) -> None:
        self.started = 0
        self.running = asyncio.Event()
        self._clock = clock
        self._runs = list(runs)

    async def __call__(self) -> Never:
        self.started += 1
        if self._runs:
            ran_for, error = self._runs.pop(0)
            self._clock.now += ran_for
            raise error
        self.running.set()
        await forever()


async def forever() -> Never:
    """Waits until cancelled."""
    await asyncio.Event().wait()
    raise AssertionError  # an event nobody sets is never set


async def stubborn() -> Never:
    """A worker that ignores the first request to stop and carries on until asked again."""
    with contextlib.suppress(asyncio.CancelledError):
        await forever()
    await forever()


@pytest.fixture
def clock() -> Moment:
    return Moment(1_000.0)


@pytest.fixture
def pause(clock: Moment) -> Pause:
    return Pause(clock)


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def supervisor(registry: CollectorRegistry, clock: Moment, pause: Pause) -> Supervisor:
    return Supervisor(
        registry=registry, permanent=(MisconfiguredError,), sleep=pause, clock=clock, draw=lambda: NO_JITTER
    )


def restarts(registry: CollectorRegistry, worker: str = NAME) -> float | None:
    return registry.get_sample_value("ledger_worker_restarts_total", {"worker": worker})


async def finish(worker: asyncio.Task[Never]) -> None:
    """Cancel a worker a test is done with, and wait for it."""
    worker.cancel()
    await asyncio.wait((worker,))


async def test_a_worker_that_fails_is_restarted_after_growing_delays_and_each_restart_counted(
    supervisor: Supervisor, clock: Moment, pause: Pause, registry: CollectorRegistry
) -> None:
    work = Flaky(clock, (0, BlipError()), (0, BlipError()), (0, BlipError()))
    with capture_logs() as logs:
        worker = supervisor.start(NAME, work)
        async with asyncio.timeout(5):
            await work.running.wait()
    assert (work.started, pause.delays, restarts(registry)) == (4, [1.0, 2.0, 4.0], 3)
    assert [(entry["event"], entry["retry_in"]) for entry in logs] == [
        ("worker.restarting", 1.0),
        ("worker.restarting", 2.0),
        ("worker.restarting", 4.0),
    ]
    assert all(isinstance(entry["exc_info"], BlipError) for entry in logs)
    assert (supervisor.dead(), worker.done()) == ((), False)
    await finish(worker)


async def test_a_worker_that_ran_long_enough_starts_its_delays_over(
    supervisor: Supervisor, clock: Moment, pause: Pause
) -> None:
    work = Flaky(clock, (0, BlipError()), (0, BlipError()), (HEALTHY_RUN, BlipError()), (0, BlipError()))
    worker = supervisor.start(NAME, work)
    async with asyncio.timeout(5):
        await work.running.wait()
    assert pause.delays == [1.0, 2.0, 1.0, 2.0]
    await finish(worker)


@pytest.mark.parametrize(
    ("in_a_row", "delay"), [(0, 1.0), (1, 2.0), (2, 4.0), (5, 32.0), (6, 60.0), (7, 60.0), (100_000, 60.0)]
)
def test_the_delays_double_up_to_their_ceiling_and_never_overflow(in_a_row: int, delay: float) -> None:
    assert restart_delay(in_a_row, NO_JITTER) == delay


def test_the_backoff_is_named() -> None:
    assert (FIRST_RESTART_DELAY, MAX_RESTART_DELAY, RESTART_JITTER, HEALTHY_RUN) == (1.0, 60.0, 0.2, 60.0)


@given(
    in_a_row=st.integers(min_value=0, max_value=10_000),
    draw=st.floats(min_value=0, max_value=1, exclude_max=True),
)
def test_jitter_shortens_a_delay_by_at_most_a_fifth_and_no_delay_passes_the_ceiling(
    in_a_row: int, draw: float
) -> None:
    """Replicas that failed together do not come back at the same instant, those at the ceiling included."""
    backoff = restart_delay(in_a_row, NO_JITTER)
    delay = restart_delay(in_a_row, draw)
    assert backoff * (1 - RESTART_JITTER) <= delay <= backoff <= MAX_RESTART_DELAY


def test_the_extremes_of_the_draw_are_the_extremes_of_the_jitter() -> None:
    assert restart_delay(0, 0.0) == 1.0
    assert restart_delay(0, 0.999_999) == pytest.approx(1.0 - RESTART_JITTER)
    assert restart_delay(100, 0.999_999) == pytest.approx(MAX_RESTART_DELAY * (1 - RESTART_JITTER))


async def test_the_jitter_is_drawn_anew_for_every_restart(
    registry: CollectorRegistry, clock: Moment, pause: Pause
) -> None:
    draws = iter([0.5, 0.25])
    supervisor = Supervisor(
        registry=registry, permanent=(), sleep=pause, clock=clock, draw=lambda: next(draws)
    )
    work = Flaky(clock, (0, BlipError()), (0, BlipError()))
    worker = supervisor.start(NAME, work)
    async with asyncio.timeout(5):
        await work.running.wait()
    assert pause.delays == pytest.approx([1.0 * (1 - RESTART_JITTER / 2), 2.0 * (1 - RESTART_JITTER / 4)])
    await finish(worker)


async def test_a_permanent_error_is_not_retried_and_leaves_the_worker_dead(
    supervisor: Supervisor, clock: Moment, pause: Pause, registry: CollectorRegistry
) -> None:
    work = Flaky(clock, (0, MisconfiguredError()))
    with capture_logs() as logs:
        worker = supervisor.start(NAME, work)
        await asyncio.wait((worker,))
    assert isinstance(worker.exception(), MisconfiguredError)
    assert (work.started, pause.delays, restarts(registry), supervisor.dead()) == (1, [], 0, (NAME,))
    (failure,) = logs
    assert isinstance(failure.pop("exc_info"), MisconfiguredError)
    assert failure == {"event": "worker.failed", "log_level": "error", "worker": NAME}


async def test_a_worker_cancelled_by_anyone_but_its_stop_is_dead(
    supervisor: Supervisor, clock: Moment
) -> None:
    work = Flaky(clock)
    worker = supervisor.start(NAME, work)
    async with asyncio.timeout(5):
        await work.running.wait()
    with capture_logs() as logs:
        await finish(worker)
    assert supervisor.dead() == (NAME,)
    assert logs == [{"event": "worker.cancelled", "log_level": "error", "worker": NAME}]


async def test_a_stopped_worker_is_cancelled_and_not_dead(supervisor: Supervisor, clock: Moment) -> None:
    work = Flaky(clock)
    worker = supervisor.start(NAME, work)
    async with asyncio.timeout(5):
        await work.running.wait()
    with capture_logs() as logs:
        await supervisor.stop(grace=5)
    assert (worker.cancelled(), supervisor.dead(), logs) == (True, (), [])


async def test_a_stop_during_the_delay_before_a_restart_restarts_nothing(
    registry: CollectorRegistry, clock: Moment
) -> None:
    waiting = asyncio.Event()

    async def wait_for_ever(seconds: float) -> None:
        del seconds
        waiting.set()
        await asyncio.Event().wait()

    supervisor = Supervisor(registry=registry, permanent=(), sleep=wait_for_ever, clock=clock)
    work = Flaky(clock, (0, BlipError()))
    worker = supervisor.start(NAME, work)
    async with asyncio.timeout(5):
        await waiting.wait()
    await supervisor.stop(grace=5)
    assert worker.cancelled()
    assert (work.started, restarts(registry), supervisor.dead()) == (1, 0, ())


async def test_a_worker_that_will_not_stop_is_left_after_its_grace_and_named(supervisor: Supervisor) -> None:
    worker = supervisor.start(NAME, stubborn)
    await asyncio.sleep(0)
    with capture_logs() as logs:
        async with asyncio.timeout(5):
            await supervisor.stop(grace=0.05)
    assert logs == [{"event": "worker.not_stopped", "log_level": "error", "worker": NAME, "grace": 0.05}]
    assert (worker.done(), supervisor.dead()) == (False, ())
    await finish(worker)


async def test_every_worker_is_exported_from_its_start_at_zero_restarts(
    supervisor: Supervisor, clock: Moment, registry: CollectorRegistry
) -> None:
    """A series that appears with its first restart hides that restart from ``rate()`` and ``increase()``."""
    workers = [supervisor.start(name, Flaky(clock)) for name in ("outbox relay", NAME)]
    assert [restarts(registry, name) for name in ("outbox relay", NAME)] == [0, 0]
    assert supervisor.workers == tuple(workers)
    for worker in workers:
        await finish(worker)


async def stops_badly() -> Never:
    """A worker whose clean-up turns its cancellation into another error."""
    try:
        await forever()
    except asyncio.CancelledError:
        raise BlipError from None


async def test_a_worker_whose_stop_ends_in_another_error_is_not_started_again(
    supervisor: Supervisor, registry: CollectorRegistry, pause: Pause
) -> None:
    """Being stopped, it is stopped, whatever it raises on the way out; what it raised is logged, once."""
    worker = supervisor.start(NAME, stops_badly)
    await asyncio.sleep(0)
    with capture_logs() as logs:
        await supervisor.stop(grace=5)
    assert isinstance(worker.exception(), BlipError)
    assert (pause.delays, restarts(registry), supervisor.dead()) == ([], 0, ())
    (failure,) = logs
    assert isinstance(failure.pop("exc_info"), BlipError)
    assert failure == {"event": "worker.failed_while_stopping", "log_level": "error", "worker": NAME}


async def test_workers_stop_together_within_one_grace_and_each_that_did_not_is_named(
    supervisor: Supervisor, clock: Moment
) -> None:
    """Three stuck workers do not take three graces: the stop waits for all of them at once."""
    grace = 0.3
    stuck = [supervisor.start(f"consumer {number}", stubborn) for number in range(3)]
    polite = supervisor.start("outbox relay", Flaky(clock))
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with capture_logs() as logs:
        await supervisor.stop(grace=grace)
    assert loop.time() - started < 2 * grace
    assert polite.cancelled()
    assert logs == [
        {"event": "worker.not_stopped", "log_level": "error", "worker": f"consumer {number}", "grace": grace}
        for number in range(3)
    ]
    for worker in stuck:
        await finish(worker)


async def test_a_stop_with_no_worker_running_waits_for_nothing(supervisor: Supervisor) -> None:
    async with asyncio.timeout(1):
        await supervisor.stop(grace=60)


@pytest.mark.parametrize(
    "error",
    [ServiceUnavailableError(), ServerError(code=500, err_code=10009)],
    ids=["no responders", "no leader"],
)
async def test_a_consumer_that_jetstream_could_not_serve_is_started_again_and_not_dead(
    registry: CollectorRegistry, clock: Moment, pause: Pause, error: Exception
) -> None:
    """With the service's permanent errors: a JetStream that cannot serve for now restarts the consumer, and
    leaves the process live; a configuration JetStream refuses leaves it dead."""
    supervisor = Supervisor(
        registry=registry, permanent=(ConsumerRefusedError, InvalidOptionError), sleep=pause, clock=clock
    )
    transient = Flaky(clock, (0, error))
    worker = supervisor.start(NAME, transient)
    async with asyncio.timeout(5):
        await transient.running.wait()
    assert (transient.started, supervisor.dead()) == (2, ())
    refused = ConsumerRefusedError(consumer=NAME, stream="EVENTS", fields=(), reason="stream not found")
    dead = supervisor.start("consumer orphan", Flaky(clock, (0, refused)))
    await asyncio.wait((dead,))
    assert supervisor.dead() == ("consumer orphan",)
    await finish(worker)
