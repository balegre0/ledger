"""Waiting, in a test, for what happens elsewhere: until a check holds, within a bound, never for a guess.

A test whose progress depends on a task of its own (a relay, a consumer, a transaction held behind a lock)
cannot await that progress: it waits until something it can observe says so. ``eventually`` polls a check;
``waits_for_a_lock`` is the check that a racing transaction is now held behind another one.
"""

import asyncio
from typing import Final
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
