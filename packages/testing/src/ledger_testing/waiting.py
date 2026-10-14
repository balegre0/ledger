"""Waiting, in a test, for what happens elsewhere: until a check holds, within a bound, never for a guess.

A test whose progress depends on a task of its own (a relay, a consumer, a transaction held behind a lock)
cannot await that progress: it waits until something it can observe says so. ``eventually`` polls a check;
``waits_for_a_lock`` is the check that a racing transaction is now held behind another one. ``Watched`` waits
for tasks that code under test started and keeps no handle on: a test never lets them run with ``sleep(0)``.
"""

import asyncio
from typing import Final, final
lazy from collections.abc import Awaitable, Callable

import psycopg
from psycopg.rows import args_row

from ledger_testing.containers import libpq_url

PATIENCE: Final = 5.0
"""Seconds ``eventually`` waits at most: a bound, not a delay; it returns as soon as the check holds."""
_POLL: Final = 0.02
_LOCK_WAITS: Final = (
    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock')"
)


async def eventually(check: Callable[[], Awaitable[bool]], *, within: float = PATIENCE) -> None:
    """Return as soon as ``check`` holds; ``TimeoutError`` if it does not within ``within`` seconds."""
    async with asyncio.timeout(within):
        while True:
            if await check():
                return
            await asyncio.sleep(_POLL)


async def waits_for_a_lock(url: str) -> bool:
    """Whether a session of the database at ``url`` (an SQLAlchemy psycopg URL) waits for a lock.

    The sessions of one role see each other in ``pg_stat_activity``, and the tests connect as the owner of
    the database they use.
    """
    async with await psycopg.AsyncConnection.connect(libpq_url(url), autocommit=True) as connection:
        cursor = connection.cursor(row_factory=args_row(bool))
        await cursor.execute(_LOCK_WAITS)
        return await cursor.fetchone() is True


class NoTaskError(AssertionError):
    """``Watched.enter`` called outside any task (from a callback): there is no task to wait for."""

    def __init__(self) -> None:
        super().__init__("Watched.enter is called from the task to watch, not from a callback")


@final
class Watched:
    """The tasks some code ran in, for a test to wait until every one has ended and been seen to end.

    The code a test hands over (a check, a worker) calls ``enter`` first. A task runs its done callbacks in
    the order they were added, so once ``ended`` returns, every callback the code under test hooked on the end
    of those tasks has run. ``ended`` must be awaited before the last of them ends.
    """

    __slots__ = ("_tasks",)

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[object]] = []

    def enter(self) -> None:
        """Watch the task this is called from; ``NoTaskError`` outside any task."""
        task = asyncio.current_task()
        if task is None:
            raise NoTaskError
        self._tasks.append(task)

    async def ended(self) -> None:
        """Return once every watched task has ended and the done callbacks added before this call have run.

        The tasks are forgotten then: what they ended with (an exception and its traceback) is not kept alive.
        """
        tasks, self._tasks = self._tasks, []
        await asyncio.wait(tasks)
