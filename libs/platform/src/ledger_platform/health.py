"""Readiness: named checks run concurrently, each held to a time limit it cannot argue with."""

import asyncio
from functools import partial
from typing import ClassVar, final
lazy from collections.abc import Awaitable, Callable

import structlog

from ledger_kernel.errors import LedgerError

type Check = Callable[[], Awaitable[object]]
"""Returns when healthy, raises when not."""

_logger = structlog.stdlib.get_logger(__name__)


class DuplicateCheckError(LedgerError):
    """Two checks registered under one name."""

    code: ClassVar[str] = "duplicate_check"

    def __init__(self, name: str) -> None:
        super().__init__(f"a health check named {name!r} already exists")


async def _call(check: Check) -> object:
    """``check`` as a coroutine, so that failing before its first ``await`` is a failed check too."""
    return await check()


@final
class Health:
    """The checks ``/readyz`` runs.

    A check is a task of its own and the probe never waits for it past its time limit: one that swallows
    the cancellation the limit asks for, or ignores it for good, is reported unhealthy and left behind
    instead of holding up the answer. A check left behind that is still running is not started again, so a
    hung dependency cannot pile up one stuck task per probe.
    """

    __slots__ = ("_abandoned", "_checks", "_timeout")

    def __init__(self, *, timeout: float = 2.0) -> None:
        self._checks: dict[str, Check] = {}
        self._timeout = timeout
        self._abandoned: dict[str, asyncio.Task[object]] = {}

    def add(self, name: str, check: Check) -> None:
        """Register ``check`` under ``name``."""
        if name in self._checks:
            raise DuplicateCheckError(name)
        self._checks[name] = check

    async def run(self) -> dict[str, bool]:
        """Every check's result; one that raises, is cancelled or exceeds the timeout is unhealthy."""
        async with asyncio.TaskGroup() as group:
            tasks = {name: group.create_task(self._one(name, check)) for name, check in self._checks.items()}
        return {name: task.result() for name, task in tasks.items()}

    async def _one(self, name: str, check: Check) -> bool:
        if name in self._abandoned:
            _logger.warning("health.check_still_running", check=name)
            return False
        probe = asyncio.create_task(_call(check))
        try:
            done, _ = await asyncio.wait((probe,), timeout=self._timeout)
        except asyncio.CancelledError:  # the probe itself is being cancelled: stop the check, pass it on
            self._abandon(name, probe)
            raise
        if not done:
            self._abandon(name, probe)
            _logger.error("health.check_timed_out", check=name)
            return False
        if probe.cancelled():  # the check raised CancelledError on its own; nobody cancelled this task
            _logger.error("health.check_cancelled", check=name)
            return False
        error = probe.exception()
        if error is not None:
            _logger.error("health.check_failed", check=name, exc_info=error)
            return False
        return True

    def _abandon(self, name: str, probe: asyncio.Task[object]) -> None:
        """Ask ``probe`` to stop and move on without waiting for it: it may never answer."""
        probe.cancel()
        self._abandoned[name] = probe
        probe.add_done_callback(partial(self._forget, name))

    def _forget(self, name: str, probe: asyncio.Task[object]) -> None:
        del self._abandoned[name]
        if not probe.cancelled():
            probe.exception()  # whatever it ended with is of no interest now; mark it retrieved
