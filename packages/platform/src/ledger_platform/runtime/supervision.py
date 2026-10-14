"""Background workers that come back: one that fails starts again, later each time, unless that cannot help.

A worker is an endless coroutine (a fan-out, an inbox or job runner) that ends only when it fails or is
cancelled (a stopped one waits for it in ``data.queue.parked``). ``Supervisor`` runs each in a task of its
own. A failure it is told is permanent (a configuration mistake) ends the worker for good. Any other is logged
with its traceback, and the worker starts again after a delay: ``FIRST_RESTART_DELAY`` seconds, twice that
after the next failure in a row, and so on up to ``MAX_RESTART_DELAY``. Each delay is shortened at random by
up to ``RESTART_JITTER`` of itself, so that replicas that failed together (a database outage) do not all come
back at the same instant, those that reached the ceiling included; no delay is ever longer than
``MAX_RESTART_DELAY``. A worker that ran for ``HEALTHY_RUN`` seconds before it failed starts its delays over.

A worker waiting to start again is not dead: only one that ended for good is (``dead``), whether by a
permanent error or because something other than ``stop`` cancelled it. Nothing brings such a worker back but a
restart of the process. A worker being stopped is never started again, whatever it ends with.

Metrics: the counter ``ledger_worker_restarts_total{worker}``, exported at zero from the worker's start.
"""

import asyncio
import random
from math import ceil, log2
from time import monotonic
from typing import ClassVar, Final, Never, final
lazy from collections.abc import Callable, Coroutine

import structlog
from prometheus_client import Counter
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import LedgerError
from ledger_platform.data.queue import being_cancelled
lazy from ledger_kernel.clock import MonotonicClock, Sleep

FIRST_RESTART_DELAY: Final = 1.0
"""Seconds before a worker that failed starts again, after its first failure in a row."""
MAX_RESTART_DELAY: Final = 60.0
"""The longest delay before a restart: a minute, however long the outage."""
RESTART_JITTER: Final = 0.2
"""How much of a delay chance may take off it."""
HEALTHY_RUN: Final = 60.0
"""Seconds a worker must have run before it failed for its delays to start over."""
_DOUBLINGS: Final = ceil(log2(MAX_RESTART_DELAY / FIRST_RESTART_DELAY))
"""How many doublings reach the ceiling: the exponent stops there, so that no count of failures overflows."""
_logger = structlog.stdlib.get_logger(__name__)

type Work = Callable[[], Coroutine[object, object, Never]]
"""Starts a run of a worker: a coroutine that never returns."""
type Draw = Callable[[], float]
"""A number drawn at random in ``[0, 1)``. A test passes its own."""


class DuplicateWorkerError(LedgerError):
    """Two workers started under one name: their logs and their metrics could not be told apart."""

    code: ClassVar[str] = "duplicate_worker"

    def __init__(self, name: str) -> None:
        super().__init__(f"a worker named {name!r} already runs")


def restart_delay(in_a_row: int, draw: float) -> float:
    """Seconds before a worker starts again after ``in_a_row`` + 1 failures in a row.

    ``draw`` in ``[0, 1)`` is the share of ``RESTART_JITTER`` taken off the backoff: 0 takes nothing, nearly 1
    takes nearly a fifth. The jitter only ever shortens, so that the ceiling holds and still does not bring
    every replica back at the same instant (a jitter both ways, capped after, would for half of them).
    """
    backoff = min(FIRST_RESTART_DELAY * 2.0 ** min(in_a_row, _DOUBLINGS), MAX_RESTART_DELAY)
    return backoff * (1 - RESTART_JITTER * draw)


@final
class Supervisor:
    """Runs a process's background workers and keeps them running (see the module).

    ``permanent`` are the errors no restart can fix.
    """

    __slots__ = ("_clock", "_dead", "_draw", "_permanent", "_restarts", "_sleep", "_stopping", "_workers")

    def __init__(
        self,
        *,
        registry: CollectorRegistry,
        permanent: tuple[type[Exception], ...],
        sleep: Sleep = asyncio.sleep,
        clock: MonotonicClock = monotonic,
        draw: Draw = random.random,
    ) -> None:
        self._permanent = permanent
        self._sleep = sleep
        self._clock = clock
        self._draw = draw
        self._workers: list[asyncio.Task[Never]] = []
        self._stopping: set[asyncio.Task[Never]] = set()
        self._dead: list[str] = []
        self._restarts = Counter(
            "ledger_worker_restarts",
            "Background workers started again after a failure.",
            ["worker"],
            registry=registry,
        )

    @property
    def workers(self) -> tuple[asyncio.Task[Never], ...]:
        """Every worker started, in the order they started."""
        return tuple(self._workers)

    def start(self, name: str, work: Work) -> asyncio.Task[Never]:
        """Run ``work`` as the worker ``name`` until it is stopped, starting it again after each failure.

        ``DuplicateWorkerError`` when a worker of that name was started already.
        """
        if any(worker.get_name() == name for worker in self._workers):
            raise DuplicateWorkerError(name)
        self._restarts.labels(name)
        worker = asyncio.create_task(self._keep(name, work), name=name)
        worker.add_done_callback(self._ended)
        self._workers.append(worker)
        return worker

    def dead(self) -> tuple[str, ...]:
        """The workers that ended for good while nobody was stopping them."""
        return tuple(self._dead)

    async def stop(self, grace: float) -> None:
        """Cancel every worker still running, and wait up to ``grace`` seconds for all of them together.

        One that does not end in time (it swallows its cancellation) is logged and left behind: stuck workers
        must not keep the rest of the process from stopping, nor take a grace each.
        """
        running = [worker for worker in self._workers if not worker.done()]
        if not running:
            return
        self._stopping.update(running)
        for worker in running:
            worker.cancel()
        _, pending = await asyncio.wait(running, timeout=grace)
        for worker in running:
            if worker in pending:
                _logger.error("worker.not_stopped", worker=worker.get_name(), grace=grace)

    async def _keep(self, name: str, work: Work) -> Never:
        in_a_row = 0
        while True:
            started = self._clock()
            try:
                await work()
            except self._permanent:
                raise
            except Exception as error:  # a worker may fail with anything; a cancellation is no Exception
                # A clean-up that turned a stop into another error: the worker is stopped all the same.
                if being_cancelled():
                    raise
                if self._clock() - started >= HEALTHY_RUN:
                    in_a_row = 0
                delay = restart_delay(in_a_row, self._draw())
                in_a_row += 1
                _logger.warning("worker.restarting", worker=name, retry_in=delay, exc_info=error)
                await self._sleep(delay)
                self._restarts.labels(name).inc()

    def _ended(self, worker: asyncio.Task[Never]) -> None:
        """Note how a worker ended, once: stopped as asked, stopped with an error, or dead.

        Every error is retrieved here and logged, so that none is reported again as never retrieved.
        """
        name = worker.get_name()
        if worker in self._stopping:
            if not worker.cancelled():
                _logger.error("worker.failed_while_stopping", worker=name, exc_info=worker.exception())
            return
        self._dead.append(name)
        if worker.cancelled():
            _logger.error("worker.cancelled", worker=name)
        else:
            _logger.error("worker.failed", worker=name, exc_info=worker.exception())
