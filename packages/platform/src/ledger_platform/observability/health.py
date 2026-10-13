"""Readiness: named checks run concurrently, each held to a time limit it cannot argue with."""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import ClassVar, Final, final
lazy from collections.abc import Awaitable, Callable

import structlog

from ledger_kernel.errors import LedgerError
from ledger_platform.config.options import require_seconds
lazy from ledger_kernel.clock import MonotonicClock

_COMPONENT: Final = "health"
"""What this module's option errors name (``InvalidOptionError.component``)."""

type Check = Callable[[], Awaitable[object]]
"""Returns when healthy, raises when not."""
type Alive = Callable[[], bool]
"""Whether a part of the process that nothing brings back once it is gone is still there; answers at once."""

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

    ``timeout`` and ``wedge_after`` are positive, finite numbers of seconds, and ``wedge_after`` is at least
    ``timeout``: a check is left behind only once it outlives its time limit. ``InvalidOptionError``
    otherwise.

    Finished runs and finished stuck checks are dropped by the next ``run`` or ``wedged``, so that the
    traceback a late failure carries is not kept for the life of the process. No callback edits the tables,
    so no two of them can disagree about whose entry it is.

    A check that starts failing is logged in full (its traceback included), once; while it keeps failing,
    each probe logs a line at debug level only, and the first probe it passes again logs
    ``health.check_recovered``.

    The liveness probe also asks the parts registered with ``add_liveness``, which are not readiness checks:
    a part that is gone for good (a background worker stopped by a configuration mistake) makes ``dead``
    name it, and nothing but a restart of the process brings it back.
    """

    __slots__ = (
        "_checks",
        "_clock",
        "_failing",
        "_flights",
        "_liveness",
        "_stuck",
        "_timeout",
        "_wedge_after",
    )

    def __init__(
        self,
        *,
        timeout: float = 2.0,
        wedge_after: float | None = None,
        clock: MonotonicClock = monotonic,
    ) -> None:
        require_seconds(_COMPONENT, "timeout", timeout)
        wedge_after = _WEDGE_TIMEOUTS * timeout if wedge_after is None else wedge_after
        require_seconds(_COMPONENT, "wedge_after", wedge_after, floor=timeout)
        self._checks: dict[str, Check] = {}
        self._timeout = timeout
        self._clock = clock
        self._wedge_after = wedge_after
        self._flights: dict[str, asyncio.Task[bool]] = {}
        self._stuck: dict[str, _Stuck] = {}
        self._liveness: dict[str, Alive] = {}
        self._failing: set[str] = set()

    def add(self, name: str, check: Check) -> None:
        """Register ``check`` under ``name``."""
        if name in self._checks:
            raise DuplicateCheckError(name)
        self._checks[name] = check

    def add_liveness(self, name: str, alive: Alive) -> None:
        """Register ``alive``, which answers whether the part ``name`` of the process is still there."""
        if name in self._liveness:
            raise DuplicateCheckError(name)
        self._liveness[name] = alive

    def dead(self) -> tuple[str, ...]:
        """The parts registered with ``add_liveness`` that are gone."""
        return tuple(name for name, alive in self._liveness.items() if not alive())

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
            self._failed(name, "health.check_still_running")
            return False
        started = self._clock()
        task = asyncio.create_task(_call(check))
        done, _ = await asyncio.wait((task,), timeout=self._timeout)
        if not done:
            self._leave(name, _Stuck(task, started))
            self._failed(name, "health.check_timed_out")
            return False
        if task.cancelled():  # the check raised CancelledError on its own; nobody cancelled this task
            self._failed(name, "health.check_cancelled")
            return False
        error = task.exception()
        if error is not None:
            self._failed(name, "health.check_failed", error)
            return False
        if name in self._failing:
            self._failing.discard(name)
            _logger.info("health.check_recovered", check=name)
        return True

    def _failed(self, name: str, event: str, error: BaseException | None = None) -> None:
        """Log that ``name`` failed: in full when it was passing, at debug level while it stays failing.

        In full is an error with the traceback, if any. An outage probed every few seconds by every prober is
        one such line, not a flood.
        """
        if name in self._failing:
            _logger.debug(event, check=name)
        elif error is None:
            self._failing.add(name)
            _logger.error(event, check=name)
        else:
            self._failing.add(name)
            _logger.error(event, check=name, exc_info=error)

    def _forget_the_finished(self) -> None:
        """Drop the runs and the stuck checks that have ended; nothing is edited while a probe awaits."""
        self._flights = {name: flight for name, flight in self._flights.items() if not flight.done()}
        self._stuck = {name: stuck for name, stuck in self._stuck.items() if not stuck.task.done()}

    def _leave(self, name: str, stuck: _Stuck) -> None:
        """Ask the check to stop and move on without waiting for it: it may never answer."""
        stuck.task.cancel()
        self._stuck[name] = stuck
        stuck.task.add_done_callback(_seen)
