"""Readiness: named checks run concurrently, each within a time limit."""

import asyncio
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


@final
class Health:
    """The checks ``/readyz`` runs."""

    __slots__ = ("_checks", "_timeout")

    def __init__(self, *, timeout: float = 2.0) -> None:
        self._checks: dict[str, Check] = {}
        self._timeout = timeout

    def add(self, name: str, check: Check) -> None:
        """Register ``check`` under ``name``."""
        if name in self._checks:
            raise DuplicateCheckError(name)
        self._checks[name] = check

    async def run(self) -> dict[str, bool]:
        """Every check's result; a check that raises or exceeds the timeout is unhealthy."""
        async with asyncio.TaskGroup() as group:
            tasks = {name: group.create_task(self._one(name, check)) for name, check in self._checks.items()}
        return {name: task.result() for name, task in tasks.items()}

    async def _one(self, name: str, check: Check) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                await check()
        except Exception:
            _logger.exception("health.check_failed", check=name)
            return False
        return True
