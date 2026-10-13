"""``Notifier`` on real PostgreSQL: NOTIFYs coalesced per channel and window, failures logged (§3.5)."""

import socket
from typing import Final
lazy from collections.abc import AsyncGenerator

import psycopg
import pytest
from structlog.testing import capture_logs

from ledger_platform.data.database import Database
from ledger_platform.data.notifier import INBOX, OUTBOX, Notifier
from ledger_platform.data.roles import APP
from ledger_testing.database import TEST_LOGINS, app_url, libpq_as
from ledger_testing.ports import port_of
lazy from ledger_testing.postgres import WorkerDatabase

END: Final = "end"


@pytest.fixture
async def database(chassis: WorkerDatabase) -> AsyncGenerator[Database]:
    opened = Database.create(app_url(chassis), pool_size=1)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
async def listener(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A worker's wake-up connection: autocommit, listening on both channels."""
    url = libpq_as(chassis, role=APP, password=TEST_LOGINS.app)
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
        await connection.execute("LISTEN ledger_outbox")
        await connection.execute("LISTEN ledger_inbox")
        yield connection


async def received(listener: psycopg.AsyncConnection) -> list[tuple[str, str]]:
    """What reached ``listener`` before the sentinel, which the test's own NOTIFY sends after the others.

    PostgreSQL delivers notifications in commit order, so the sentinel bounds the read without any sleep.
    """
    await listener.execute("NOTIFY ledger_outbox, 'end'")
    seen: list[tuple[str, str]] = []
    async for notify in listener.notifies(timeout=10):
        if notify.payload == END:
            break
        seen.append((notify.channel, notify.payload))
    return seen


async def test_signals_within_the_window_become_one_notify_per_channel(
    database: Database, listener: psycopg.AsyncConnection
) -> None:
    notifier = Notifier(database, window=0.05)
    for channel in (OUTBOX, OUTBOX, INBOX, OUTBOX):
        notifier.signal(channel)
    await notifier.aclose()
    assert sorted(await received(listener)) == [("ledger_inbox", ""), ("ledger_outbox", "")]


async def test_a_signal_after_the_window_closed_sends_again(
    database: Database, listener: psycopg.AsyncConnection
) -> None:
    """A commit after a NOTIFY went out is news the listener has not had: it is never coalesced away."""
    notifier = Notifier(database, window=0.0)
    notifier.signal(OUTBOX)
    await notifier.aclose()
    notifier.signal(OUTBOX)
    await notifier.aclose()
    assert await received(listener) == [("ledger_outbox", ""), ("ledger_outbox", "")]


async def test_a_failed_notify_is_logged_and_not_raised() -> None:
    with socket.create_server(("127.0.0.1", 0)) as closed:
        port = port_of(closed)
    unreachable = Database.create(f"postgresql+psycopg://nobody:nothing@127.0.0.1:{port}/ledger", pool_size=1)
    notifier = Notifier(unreachable, window=0.0)
    try:
        with capture_logs() as logs:
            notifier.signal(INBOX)
            await notifier.aclose()
            notifier.signal(INBOX)  # a failed channel is not left pending: the next signal tries again
            await notifier.aclose()
    finally:
        await unreachable.aclose()
    assert [(log["event"], log["channel"], log["log_level"]) for log in logs] == [
        ("notifier.notify_failed", "ledger_inbox", "error")
    ] * 2
