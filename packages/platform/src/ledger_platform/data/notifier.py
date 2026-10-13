"""After-commit NOTIFYs, coalesced: at most one per channel per window per process (§3.5).

A commit that wrote outbox rows signals ``ledger_outbox``; the fan-out signals ``ledger_inbox``. Workers
LISTEN on both (``ledger_platform.events.Wakeups``); the one-second poll is the safety net, so a NOTIFY that
fails is logged and dropped, never raised. A signal that arrives while a NOTIFY of its channel waits for its
window is folded into it; one that arrives after that NOTIFY left its window schedules another, so no commit
goes unannounced.
"""

import asyncio
from typing import Final, Literal, Protocol, final, override

import structlog
from sqlalchemy import text

lazy from ledger_platform.data.database import Database

type Channel = Literal["ledger_outbox", "ledger_inbox"]
OUTBOX: Final[Channel] = "ledger_outbox"
INBOX: Final[Channel] = "ledger_inbox"
DEFAULT_WINDOW: Final = 0.01
"""Seconds: the spec's at most one NOTIFY per channel per 10 ms."""
_NOTIFY: Final = text("SELECT pg_notify(:channel, '')")
_logger = structlog.stdlib.get_logger(__name__)


class Signals(Protocol):
    """What a unit tells after it commits (tests record it; ``Notifier`` sends it)."""

    def signal(self, channel: Channel) -> None:
        """Say that ``channel`` has news, after a commit."""


@final
class Notifier(Signals):
    """Sends a NOTIFY per channel at most once per ``window`` seconds, on a pooled connection."""

    __slots__ = ("_database", "_pending", "_tasks", "_window")

    def __init__(self, database: Database, *, window: float = DEFAULT_WINDOW) -> None:
        self._database = database
        self._window = window
        self._pending: set[Channel] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @override
    def signal(self, channel: Channel) -> None:
        """Schedule one NOTIFY on ``channel`` unless one is already waiting to go."""
        if channel in self._pending:
            return
        self._pending.add(channel)
        task = asyncio.get_running_loop().create_task(self._send(channel))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, channel: Channel) -> None:
        try:
            await asyncio.sleep(self._window)  # the coalescing window, not a wait for anything to happen
        finally:
            self._pending.discard(channel)  # from here a signal is news this NOTIFY may not carry
        try:
            async with self._database.engine.connect() as connection:
                await connection.execute(_NOTIFY, {"channel": channel})
                await connection.commit()
        except Exception:
            _logger.exception("notifier.notify_failed", channel=channel)

    async def aclose(self) -> None:
        """Wait for the NOTIFYs already scheduled (each one logs its own failure)."""
        await asyncio.gather(*self._tasks)
