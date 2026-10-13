"""Wake-ups on real PostgreSQL (§3.5, §6.7): LISTEN, the poll, a lost connection, the runners they wake.

Nothing here sleeps to prove that something did not happen: delivery order is NOTIFY's own (commit order on
one connection), and waits are bounded checks of something that does happen (``eventually``).
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Awaitable, Callable, Mapping

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import text

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.notifier import Notifier
from ledger_platform.data.roles import APP
from ledger_platform.events import Consumer, Dispatcher, InboxRunner, Wakeups
from ledger_testing.database import TEST_LOGINS, libpq_as
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Channel, Signals
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events import RawEvent
lazy from ledger_testing.postgres import WorkerDatabase

type Eventually = Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]
type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]

_ID: Final = TypeAdapter(uuid.UUID)
TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
TERMINATE: Final = """
    SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
    WHERE datname = current_database() AND query LIKE 'LISTEN %%'
"""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


@pytest.fixture
def wakeups(chassis: WorkerDatabase) -> Wakeups:
    return Wakeups(libpq_as(chassis, role=APP, password=TEST_LOGINS.app))


async def test_a_notify_wakes_its_subscribers_only(
    wakeups: Wakeups, superuser: psycopg.AsyncConnection, eventually: Eventually
) -> None:
    inbox_a, inbox_b = wakeups.subscribe("ledger_inbox"), wakeups.subscribe("ledger_inbox")
    outbox = wakeups.subscribe("ledger_outbox")
    task: asyncio.Task[object] = asyncio.create_task(wakeups.run())

    async def connected() -> bool:
        return wakeups.connected

    try:
        await eventually(connected)
        # a (re)connection wakes every subscriber once: what was notified while it was down is not lost
        assert inbox_a.is_set()
        assert inbox_b.is_set()
        assert outbox.is_set()
        for event in (inbox_a, inbox_b, outbox):
            event.clear()
        await superuser.execute("NOTIFY ledger_inbox")

        async def inbox_woken() -> bool:
            return inbox_a.is_set() and inbox_b.is_set()

        await eventually(inbox_woken)
        # the inbox NOTIFY has been dispatched in full (one synchronous step) and no other was sent yet
        assert not outbox.is_set()
        await superuser.execute("NOTIFY ledger_outbox, 'x'")

        async def outbox_woken() -> bool:
            return outbox.is_set()

        await eventually(outbox_woken)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_wait_returns_on_the_poll_when_nothing_arrives(wakeups: Wakeups) -> None:
    event = wakeups.subscribe("ledger_inbox")
    await wakeups.wait(event, poll=0.05)
    assert not event.is_set()


async def test_wait_returns_at_once_when_woken_and_clears_the_event(wakeups: Wakeups) -> None:
    event = wakeups.subscribe("ledger_outbox")
    event.set()
    async with asyncio.timeout(5):
        await wakeups.wait(event, poll=60)
    assert not event.is_set()


async def test_a_lost_connection_is_raised_for_the_supervisor_and_a_restart_listens_again(
    wakeups: Wakeups, superuser: psycopg.AsyncConnection, eventually: Eventually
) -> None:
    woken = wakeups.subscribe("ledger_inbox")
    task: asyncio.Task[object] = asyncio.create_task(wakeups.run())

    async def connected() -> bool:
        return wakeups.connected

    await eventually(connected)
    await superuser.execute(TERMINATE.encode())
    with pytest.raises(psycopg.OperationalError):
        await task
    assert not wakeups.connected
    restarted: asyncio.Task[object] = asyncio.create_task(wakeups.run())  # what the supervisor does
    try:
        await eventually(connected)
        assert woken.is_set()  # the reconnection's wake-up
        woken.clear()
        await superuser.execute("NOTIFY ledger_inbox")

        async def woken_up() -> bool:
            return woken.is_set()

        await eventually(woken_up)
    finally:
        restarted.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await restarted


async def test_a_commit_wakes_the_fan_out_and_the_fan_out_wakes_the_consumer(
    pool: Database,
    other: BoundDatabase,
    wakeups: Wakeups,
    subscribe: Subscribe,
    eventually: Eventually,
) -> None:
    subscribe("other", {"other.renames": frozenset({Renamed.TYPE})})
    notifier = Notifier(pool)
    probe = BoundDatabase(
        pool, schema="probe", source="probe", publishes=frozenset({Renamed.TYPE}), signals=notifier
    )
    seen: list[uuid.UUID | None] = []

    async def handle(event: RawEvent, unit: UnitOfWork) -> None:
        del unit
        seen.append(event.subject)

    consumer = Consumer(
        name="other.renames", types=frozenset({Renamed.TYPE}), max_attempts=10, ordered=False, handler=handle
    )
    workers: list[asyncio.Task[object]] = [
        asyncio.create_task(wakeups.run()),
        asyncio.create_task(Dispatcher(pool, schemas=("probe", "other"), signals=notifier).run(wakeups)),
        asyncio.create_task(InboxRunner(database=other, consumer=consumer).run(wakeups)),
    ]
    subject = uuid.uuid7()
    try:

        async def connected() -> bool:
            return wakeups.connected

        await eventually(connected)
        async with probe.unit(TENANT) as uow:
            uow.publish(Renamed(scope=TENANT, subject=subject, aggregate_version=1, name="x"))

        async def handled() -> bool:
            return seen == [subject]

        await eventually(handled)
    finally:
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await notifier.aclose()


class StopLoopError(Exception):
    """Ends a runner's loop at the wait the test chose."""

    def __init__(self) -> None:
        super().__init__("the test stops the loop here")


@final
class Countdown:
    """A ``Waker`` that records each wait and ends the loop at the ``last`` one."""

    def __init__(self, last: int) -> None:
        self.last = last
        self.subscribed: list[str] = []
        self.polls: list[float] = []

    def subscribe(self, channel: Channel) -> asyncio.Event:
        self.subscribed.append(channel)
        return asyncio.Event()

    async def wait(self, event: asyncio.Event, *, poll: float = 1.0) -> None:
        del event
        self.polls.append(poll)
        if len(self.polls) == self.last:
            raise StopLoopError


async def test_each_runner_drains_then_waits_for_its_own_channel_or_the_poll(
    pool: Database, probe: BoundDatabase, other: BoundDatabase, subscribe: Subscribe, signals: Signals
) -> None:
    subscribe("other", {"other.renames": frozenset({Renamed.TYPE})})
    async with probe.unit(TENANT) as uow:
        uow.publish(Renamed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name="x"))
    fan_out = Countdown(2)
    with pytest.raises(StopLoopError):
        await Dispatcher(pool, schemas=("probe", "other"), signals=signals).run(fan_out)
    seen: list[str] = []

    async def handle(event: RawEvent, unit: UnitOfWork) -> None:
        del unit
        seen.append(event.type)

    consumer = Consumer(
        name="other.renames", types=frozenset({Renamed.TYPE}), max_attempts=10, ordered=False, handler=handle
    )
    inbox = Countdown(2)
    with pytest.raises(StopLoopError):
        await InboxRunner(database=other, consumer=consumer).run(inbox)
    assert (fan_out.subscribed, fan_out.polls) == (["ledger_outbox"], [1.0, 1.0])
    assert (inbox.subscribed, inbox.polls) == (["ledger_inbox"], [1.0, 1.0])
    assert seen == [Renamed.TYPE]


async def test_a_replay_wakes_the_inbox_runners(
    wakeups: Wakeups,
    probe: BoundDatabase,
    other: BoundDatabase,
    subscribe: Subscribe,
    eventually: Eventually,
) -> None:
    subscribe("other", {"other.renames": frozenset({Renamed.TYPE})})
    async with probe.unit(TENANT) as uow:
        uow.publish(Renamed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name="x"))
    await Dispatcher(probe.database, schemas=("probe", "other"), signals=Silent()).run_until_idle()
    async with other.unit(None) as uow:
        found = await uow.connection.execute(
            text("UPDATE other.inbox SET status = 'dead' RETURNING event_id")
        )
        event_id = _ID.validate_python(found.scalar_one())

    async def handle(event: RawEvent, unit: UnitOfWork) -> None:
        del event, unit

    consumer = Consumer(
        name="other.renames", types=frozenset({Renamed.TYPE}), max_attempts=10, ordered=False, handler=handle
    )
    woken = wakeups.subscribe("ledger_inbox")
    task: asyncio.Task[object] = asyncio.create_task(wakeups.run())

    async def connected() -> bool:
        return wakeups.connected

    async def woken_up() -> bool:
        return woken.is_set()

    try:
        await eventually(connected)
        woken.clear()  # the connection's own wake-up
        assert await InboxRunner(database=other, consumer=consumer).replay(event_id)
        await eventually(woken_up)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@final
class Silent:
    def signal(self, channel: Channel) -> None:
        del channel
