"""After-commit NOTIFYs, coalesced: at most one per channel per window per process (§3.5).

A commit that wrote outbox rows signals ``ledger_outbox``; the fan-out signals ``ledger_inbox``. Workers
LISTEN on both (``ledger_platform.events.Wakeups``); the one-second poll is the safety net, so a NOTIFY that
fails is logged and dropped, never raised.

Each channel has at most one sender. A signal marks its channel dirty and starts the sender if none runs; the
sender waits out the window, clears the mark, sends, and goes round again while the mark is back. Signals that
arrive while a NOTIFY waits for a connection (a busy pool) are folded into one more NOTIFY after it, never
into a task each, and no commit goes unannounced.
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
    """Sends a NOTIFY per channel at most once per ``window`` seconds, one sender per channel.

    The NOTIFYs go through ``database``'s pool; a dedicated connection is the runtime's choice (``App``).
    """

    __slots__ = ("_database", "_dirty", "_senders", "_window")

    def __init__(self, database: Database, *, window: float = DEFAULT_WINDOW) -> None:
        self._database = database
        self._window = window
        self._dirty: set[Channel] = set()
        self._senders: dict[Channel, asyncio.Task[None]] = {}

    @override
    def signal(self, channel: Channel) -> None:
        """Mark ``channel`` dirty; start its sender unless one runs (it will see the mark)."""
        self._dirty.add(channel)
        sender = self._senders.get(channel)
        if sender is None or sender.done():  # done: cancelled before it ever ran, so it never cleaned up
            self._senders[channel] = asyncio.get_running_loop().create_task(self._send(channel))

    async def _send(self, channel: Channel) -> None:
        try:
            while channel in self._dirty:
                await asyncio.sleep(self._window)  # the coalescing window, not a wait for anything to happen
                self._dirty.discard(channel)  # from here a signal is news this NOTIFY may not carry
                try:
                    async with self._database.engine.connect() as connection:
                        await connection.execute(_NOTIFY, {"channel": channel})
                        await connection.commit()
                except Exception:
                    _logger.exception("notifier.notify_failed", channel=channel)
        finally:
            del self._senders[channel]

    async def aclose(self) -> None:
        """Wait for the senders running now (each logs its own failure; a cancelled one is let go)."""
        await asyncio.gather(*self._senders.values(), return_exceptions=True)
