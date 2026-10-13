"""Wake-ups (§3.5, §6.7): one LISTEN connection per process, opened directly to PostgreSQL.

``run()`` connects, LISTENs on ``ledger_outbox`` and ``ledger_inbox`` and sets every subscriber's event when a
NOTIFY arrives; when the connection is lost it raises, and the worker's supervisor starts it again
(subscribers stay subscribed across restarts). Runners never depend on it: they wait for their event *or*
the poll, whichever comes first, so a NOTIFY lost while the connection was down costs at most one poll.
"""

import asyncio
import contextlib
from abc import ABCMeta, abstractmethod
from typing import Final, Never, Protocol, final

import psycopg

from ledger_platform.data.notifier import INBOX, OUTBOX
lazy from ledger_platform.data.notifier import Channel

_LISTEN: Final = (f"LISTEN {OUTBOX}", f"LISTEN {INBOX}")


class Waker(Protocol, metaclass=ABCMeta):
    """What a runner's loop needs of the wake-ups: an event per channel, and a wait bounded by the poll."""

    @abstractmethod
    def subscribe(self, channel: Channel) -> asyncio.Event:
        """An event set whenever ``channel`` is notified."""

    @abstractmethod
    async def wait(self, event: asyncio.Event, *, poll: float = 1.0) -> None:
        """Return when ``event`` is set or after ``poll`` seconds; ``event`` is cleared either way."""


@final
class Wakeups:
    """See the module. ``url`` is a libpq URL (``postgresql://``) that bypasses any transaction pooler."""

    __slots__ = ("_connected", "_subscribers", "_url")

    def __init__(self, url: str) -> None:
        self._url = url
        self._subscribers: dict[str, list[asyncio.Event]] = {OUTBOX: [], INBOX: []}
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the LISTEN connection is up (the worker's readiness)."""
        return self._connected

    def subscribe(self, channel: Channel) -> asyncio.Event:
        """An event set whenever ``channel`` is notified."""
        event = asyncio.Event()
        self._subscribers[channel].append(event)
        return event

    @staticmethod
    async def wait(event: asyncio.Event, *, poll: float = 1.0) -> None:
        """Return when ``event`` is set or after ``poll`` seconds; ``event`` is cleared either way."""
        try:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(poll):
                    await event.wait()
        finally:
            event.clear()

    async def run(self) -> Never:
        """Listen until the connection fails, then raise its error (see the module)."""
        try:
            async with await psycopg.AsyncConnection.connect(self._url, autocommit=True) as connection:
                for statement in _LISTEN:
                    await connection.execute(statement.encode())
                self._connected = True
                notifications = connection.notifies()
                while True:
                    notification = await anext(notifications)
                    for event in self._subscribers.get(notification.channel, ()):
                        event.set()
        finally:
            self._connected = False
