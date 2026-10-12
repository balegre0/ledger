"""Readiness: named checks run concurrently, each held to a time limit it cannot argue with."""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import ClassVar, Final, final
lazy from collections.abc import Awaitable, Callable

import structlog

from ledger_kernel.errors import LedgerError

type Check = Callable[[], Awaitable[object]]
"""Returns when healthy, raises when not."""

_WEDGE_TIMEOUTS: Final = 10
"""How many timeouts a check may outlive before the process counts as wedged, unless told otherwise."""
_logger = structlog.stdlib.get_logger(__name__)


class DuplicateCheckError(LedgerError):
    """Two checks registered under one name."""

    code: ClassVar[str] = "duplicate_check"

    def __init__(self, name: str) -> None:
        super().__init__(f"a health check named {name!r} already exists")


@final
@dataclass(frozen=True, slots=True)
class _Stuck:
    """A check that outlived its time limit and was asked to stop, started at ``started`` (monotonic)."""

    task: asyncio.Task[object]
    started: float


async def _call(check: Check) -> object:
    """``check`` as a coroutine, so that failing before its first ``await`` is a failed check too."""
    return await check()


def _seen(task: asyncio.Task[object]) -> None:
    """Nobody awaits a check that was left behind: whatever it ends with counts as seen."""
    if not task.cancelled():
        task.exception()


@final
class Health:
    """The checks ``/readyz`` runs, and the verdict on whether the process itself is still sound.

    A check is a task of its own and no probe waits for it past its time limit: one that swallows the
    cancellation the limit asks for, or ignores it for good, is reported unhealthy and left behind instead
    of holding up the answer. Probes that overlap (a kubelet and a load balancer) share one run of each
    check, so a slow dependency is asked once, not once per prober.

    A check left behind is cancelled again by every later probe and is not started again while it lives, so a
    hung dependency cannot pile up one stuck task per probe. One that outlives ``wedge_after`` seconds
    (by default ten timeouts) means the process cannot be trusted to recover: ``wedged`` names it, for the
    liveness probe to ask for a restart. Only checks that ``run`` has started can be found wedged: a
    service whose readiness is never probed is never told that a check hangs. ``clock`` is the monotonic
    time that ages a stuck check, and a test moves its own.

    Finished runs and finished stuck checks are dropped by the next ``run`` or ``wedged``, so that the
    traceback a late failure carries is not kept for the life of the process. No callback edits the tables,
    so no two of them can disagree about whose entry it is.
    """

    __slots__ = ("_checks", "_clock", "_flights", "_stuck", "_timeout", "_wedge_after")

    def __init__(
        self,
        *,
        timeout: float = 2.0,
        wedge_after: float | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._checks: dict[str, Check] = {}
        self._timeout = timeout
        self._clock = clock
        self._wedge_after = _WEDGE_TIMEOUTS * timeout if wedge_after is None else wedge_after
        self._flights: dict[str, asyncio.Task[bool]] = {}
        self._stuck: dict[str, _Stuck] = {}

    def add(self, name: str, check: Check) -> None:
        """Register ``check`` under ``name``."""
        if name in self._checks:
            raise DuplicateCheckError(name)
        self._checks[name] = check

    def wedged(self) -> tuple[str, ...]:
        """The checks that have outlived ``wedge_after`` seconds and still will not stop.

        Only those a ``run`` has started: a check nobody has asked for is not watched.
        """
        self._forget_the_finished()
        now = self._clock()
        return tuple(name for name, stuck in self._stuck.items() if now - stuck.started > self._wedge_after)

    async def run(self) -> dict[str, bool]:
        """Every check's result; one that raises, is cancelled or exceeds the timeout is unhealthy."""
        self._forget_the_finished()
        async with asyncio.TaskGroup() as group:
            tasks = {
                name: group.create_task(self._verdict(name, check)) for name, check in self._checks.items()
            }
        return {name: task.result() for name, task in tasks.items()}

    async def _verdict(self, name: str, check: Check) -> bool:
        """The result of ``name`` from the run under way, or from a run begun now.

        The run is shielded: a prober that gives up (its own request cancelled) must not cancel the check for
        the probers still waiting, and the run enforces its own time limit whether anyone waits or not.
        """
        flight = self._flights.get(name)
        if flight is None or flight.done():
            flight = self._flights[name] = asyncio.create_task(self._probe(name, check))
        return await asyncio.shield(flight)

    async def _probe(self, name: str, check: Check) -> bool:
        stuck = self._stuck.get(name)
        if stuck is not None and not stuck.task.done():
            stuck.task.cancel()  # again: the last cancellation may have reached a check that was not looking
            _logger.warning("health.check_still_running", check=name)
            return False
        started = self._clock()
        task = asyncio.create_task(_call(check))
        done, _ = await asyncio.wait((task,), timeout=self._timeout)
        if not done:
            self._leave(name, _Stuck(task, started))
            _logger.error("health.check_timed_out", check=name)
            return False
        if task.cancelled():  # the check raised CancelledError on its own; nobody cancelled this task
            _logger.error("health.check_cancelled", check=name)
            return False
        error = task.exception()
        if error is not None:
            _logger.error("health.check_failed", check=name, exc_info=error)
            return False
        return True

    def _forget_the_finished(self) -> None:
        """Drop the runs and the stuck checks that have ended; nothing is edited while a probe awaits."""
        self._flights = {name: flight for name, flight in self._flights.items() if not flight.done()}
        self._stuck = {name: stuck for name, stuck in self._stuck.items() if not stuck.task.done()}

    def _leave(self, name: str, stuck: _Stuck) -> None:
        """Ask the check to stop and move on without waiting for it: it may never answer."""
        stuck.task.cancel()
        self._stuck[name] = stuck
        stuck.task.add_done_callback(_seen)
