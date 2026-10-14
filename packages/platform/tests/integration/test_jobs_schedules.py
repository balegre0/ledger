"""``Scheduler`` on real PostgreSQL: one job per slot across replicas, through lease rows (§3.6).

Time is the database's: a test makes a slot due, or a lease run out, by moving the lease row as admin.
"""

import asyncio
import contextlib
import os
import socket
from datetime import UTC, timedelta
from functools import partial
from typing import Final
lazy from collections.abc import AsyncGenerator, Callable
lazy from datetime import datetime

import psycopg
import pytest
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import event
from structlog.testing import capture_logs

from ledger_kernel.errors import InvalidValueError
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.unit_of_work import JobPolicy, UndeclaredJobKindError
from ledger_platform.jobs.schedules import Schedule, Scheduler
from ledger_testing import waiting
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.postgres import WorkerDatabase

NIGHTLY: Final = "probe.nightly"
EXPORT: Final = "probe.export"
DAY: Final = timedelta(hours=24)
STEAL: Final = """
    UPDATE platform.schedule_leases SET holder = 'b', leased_until = now() + interval '1 hour'
    RETURNING leased_until
"""
MOVE: Final = """
    UPDATE platform.schedule_leases SET next_run_at = next_run_at + interval '1 hour'
    RETURNING holder, next_run_at, leased_until
"""
_LEASE: Final = TypeAdapter(tuple[AwareDatetime, str | None, AwareDatetime | None])
_KEYS: Final = TypeAdapter(list[tuple[str, str, str]])


class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@pytest.fixture
async def databases(chassis: WorkerDatabase) -> AsyncGenerator[tuple[Database, Database]]:
    first, second = (
        Database.create(app_url(chassis), pool_size=2),
        Database.create(app_url(chassis), pool_size=2),
    )
    try:
        yield first, second
    finally:
        await first.aclose()
        await second.aclose()


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


def bound(database: Database) -> tuple[BoundDatabase, BoundDatabase]:
    """The platform's bound database, and ``probe``'s, which declares ``probe.export``."""
    platform = BoundDatabase(database, schema="platform", source="platform", signals=Silent())
    probe = BoundDatabase(
        database,
        schema="probe",
        source="probe",
        jobs={EXPORT: JobPolicy(max_attempts=3, deadline=None)},
        signals=Silent(),
    )
    return platform, probe


def scheduler(database: Database, holder: str) -> Scheduler:
    platform, probe = bound(database)
    return Scheduler(platform=platform, schedules=[Schedule(NIGHTLY, DAY, EXPORT, probe)], holder=holder)


@pytest.fixture
def first(databases: tuple[Database, Database]) -> Scheduler:
    return scheduler(databases[0], "a")


async def lease(admin: psycopg.AsyncConnection) -> tuple[datetime, str | None, datetime | None]:
    cursor = await admin.execute("SELECT next_run_at, holder, leased_until FROM platform.schedule_leases")
    (row,) = await cursor.fetchall()
    return _LEASE.validate_python(tuple(row))


async def jobs(admin: psycopg.AsyncConnection) -> list[tuple[str, str, str]]:
    cursor = await admin.execute("SELECT kind, key, status FROM probe.jobs ORDER BY created_at, key")
    return _KEYS.validate_python([tuple(row) for row in await cursor.fetchall()])


def key(slot: datetime) -> str:
    return f"{NIGHTLY}@{slot.astimezone(UTC).isoformat()}"


async def test_a_schedule_enqueues_one_job_per_slot(first: Scheduler, admin: psycopg.AsyncConnection) -> None:
    assert await first.run_due() == 1
    following, holder, leased_until = await lease(admin)
    slot = following - DAY  # the first slot: the lease row's first sight
    assert (holder, leased_until) == (None, None)
    assert await jobs(admin) == [(EXPORT, key(slot), "ready")]
    assert await first.run_due() == 0
    await admin.execute("UPDATE platform.schedule_leases SET next_run_at = now() - interval '1 second'")
    assert await first.run_due() == 1
    (_, _, _), (_, second_key, _) = await jobs(admin)
    then, _, _ = await lease(admin)
    assert second_key == key(then - DAY)
    assert second_key != key(slot)


async def test_missed_slots_are_not_replayed(first: Scheduler, admin: psycopg.AsyncConnection) -> None:
    assert await first.run_due() == 1
    following, _, _ = await lease(admin)
    await admin.execute("UPDATE platform.schedule_leases SET next_run_at = next_run_at - interval '96 hours'")
    assert await first.run_due() == 1  # three periods down: one job, for the oldest slot missed
    assert len(await jobs(admin)) == 2
    after, _, _ = await lease(admin)
    assert after == following  # the first slot after now, not the next missed one
    assert await first.run_due() == 0


async def test_two_schedulers_fire_a_slot_once(
    first: Scheduler, databases: tuple[Database, Database], admin: psycopg.AsyncConnection
) -> None:
    second = scheduler(databases[1], "b")
    assert sum(await asyncio.gather(first.run_due(), second.run_due())) == 1
    assert len(await jobs(admin)) == 1


async def test_a_lease_held_by_a_crashed_scheduler_expires(
    first: Scheduler, admin: psycopg.AsyncConnection
) -> None:
    assert await first.run_due() == 1
    await admin.execute(
        """
        UPDATE platform.schedule_leases
        SET holder = 'dead', leased_until = now() + interval '30 seconds',
            next_run_at = now() - interval '1 second'
        """
    )
    assert await first.run_due() == 0
    await admin.execute("UPDATE platform.schedule_leases SET leased_until = now() - interval '1 second'")
    assert await first.run_due() == 1
    assert (await lease(admin))[1:] == (None, None)


async def test_a_scheduler_whose_lease_was_taken_over_moves_nothing(
    databases: tuple[Database, Database], chassis: WorkerDatabase, admin: psycopg.AsyncConnection
) -> None:
    """I2: while ``a`` enqueues, ``b`` takes the lease; ``a``'s release leaves the row as ``b`` holds it."""
    stolen: list[object] = []

    def steal(*arguments: object) -> None:
        """``before_cursor_execute``: steal the lease, as admin, just before the slot's job is written."""
        if not stolen and "INSERT INTO probe.jobs" in str(arguments[2]):
            with psycopg.connect(chassis.admin_url(), autocommit=True) as connection:
                taken = connection.execute(STEAL).fetchone()
            stolen.append(None if taken is None else taken[0])

    event.listen(databases[0].engine.sync_engine, "before_cursor_execute", steal)
    assert await scheduler(databases[0], "a").run_due() == 1
    ((_, slot_key, _),) = await jobs(admin)
    next_run_at, holder, leased_until = await lease(admin)
    assert (holder, leased_until) == ("b", stolen[0])
    assert slot_key == key(next_run_at)  # still the slot ``a`` fired: ``b`` will move it


async def test_run_fires_the_due_slots_poll_after_poll(
    first: Scheduler, admin: psycopg.AsyncConnection
) -> None:
    task = asyncio.create_task(first.run())

    async def fired(count: int) -> bool:
        """``count`` jobs enqueued, and the slot released (else the release would undo the test's move)."""
        return len(await jobs(admin)) == count and (await lease(admin))[1] is None

    try:
        await waiting.eventually(partial(fired, 1))
        await admin.execute("UPDATE platform.schedule_leases SET next_run_at = now() - interval '1 second'")
        await waiting.eventually(partial(fired, 2))  # the next poll took the next slot
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_a_schedule_runs_at_a_positive_interval(
    first: Scheduler, databases: tuple[Database, Database]
) -> None:
    del first
    target = BoundDatabase(databases[0], schema="probe", source="probe", signals=Silent())
    with pytest.raises(InvalidValueError, match=r"schedule\.every"):
        Schedule(NIGHTLY, timedelta(0), EXPORT, target)


def listening(database: Database, act: Callable[[str], None]) -> None:
    """Run ``act`` with each statement's text before the pool sends it (``before_cursor_execute``)."""

    def heard(*arguments: object) -> None:
        act(str(arguments[2]))

    event.listen(database.engine.sync_engine, "before_cursor_execute", heard)


async def test_a_slot_fires_once_even_after_its_job_finished(
    first: Scheduler, admin: psycopg.AsyncConnection
) -> None:
    """M2: a slot taken again (a crash before the release) never restarts its finished job."""
    assert await first.run_due() == 1
    await admin.execute("UPDATE probe.jobs SET status = 'done'")
    await admin.execute("UPDATE platform.schedule_leases SET next_run_at = next_run_at - interval '24 hours'")
    slot, _, _ = await lease(admin)
    assert await first.run_due() == 1
    assert await jobs(admin) == [(EXPORT, key(slot), "done")]


async def test_a_scheduler_holds_leases_under_a_name_of_its_own_and_releases_only_its_slot(
    databases: tuple[Database, Database], chassis: WorkerDatabase, admin: psycopg.AsyncConnection
) -> None:
    """M3: the default holder names the host, the process and the instance; the release names the slot."""
    seen: list[tuple[object, ...]] = []

    def move_the_slot(statement: str) -> None:
        if not seen and "INSERT INTO probe.jobs" in statement:
            with psycopg.connect(chassis.admin_url(), autocommit=True) as connection:
                row = connection.execute(MOVE).fetchone()
            seen.append(() if row is None else tuple(row))

    listening(databases[0], move_the_slot)
    platform, probe = bound(databases[0])
    one = Scheduler(platform=platform, schedules=[Schedule(NIGHTLY, DAY, EXPORT, probe)])
    two = Scheduler(platform=platform, schedules=[Schedule(NIGHTLY, DAY, EXPORT, probe)])
    assert await one.run_due() == 1
    ((holder, moved, leased_until),) = seen
    assert isinstance(holder, str)
    assert holder.startswith(f"{socket.gethostname()}:{os.getpid()}:")
    assert await lease(admin) == (moved, holder, leased_until)  # not the slot it took: left alone
    assert holder != two.holder


async def test_a_schedule_names_a_job_kind_its_target_declares(databases: tuple[Database, Database]) -> None:
    """M4: checked when the schedule is made, not when its first slot fails."""
    _, probe = bound(databases[0])
    with pytest.raises(UndeclaredJobKindError):
        Schedule(NIGHTLY, DAY, "probe.unknown", probe)


async def test_a_failing_schedule_never_keeps_the_others_from_firing(
    databases: tuple[Database, Database], admin: psycopg.AsyncConnection
) -> None:
    """M4: a schedule whose target cannot take its job is logged; the next one still fires."""
    platform, probe = bound(databases[0])
    missing = BoundDatabase(
        databases[0],
        schema="missing",
        source="missing",
        jobs={EXPORT: JobPolicy(max_attempts=3, deadline=None)},
        signals=Silent(),
    )
    schedules = [Schedule("missing.nightly", DAY, EXPORT, missing), Schedule(NIGHTLY, DAY, EXPORT, probe)]
    with capture_logs() as logs:
        fired = await Scheduler(platform=platform, schedules=schedules, holder="a").run_due()
    assert fired == 1
    assert [(log["event"], log["schedule"]) for log in logs] == [("schedule_failed", "missing.nightly")]
    assert len(await jobs(admin)) == 1


async def test_lease_rows_are_made_once_per_scheduler(databases: tuple[Database, Database]) -> None:
    """M5: the first-sight upsert runs on the first pass only, not on every poll."""
    inserts: list[str] = []
    listening(
        databases[0],
        lambda statement: inserts.extend(
            [statement] if "INSERT INTO platform.schedule_leases" in statement else []
        ),
    )
    one = scheduler(databases[0], "a")
    await one.run_due()
    await one.run_due()
    assert len(inserts) == 1


REFUSE_LEASES: Final = """
    CREATE FUNCTION platform.refuse_leases() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'refused by the test'; END $$
"""
REFUSE_TRIGGER: Final = """
    CREATE TRIGGER refuse_leases BEFORE INSERT ON platform.schedule_leases
    FOR EACH ROW EXECUTE FUNCTION platform.refuse_leases()
"""


async def test_a_failure_making_the_lease_rows_is_logged_and_tried_again_at_the_next_poll(
    databases: tuple[Database, Database], admin: psycopg.AsyncConnection
) -> None:
    """N3: ``run`` must not end on it: the next poll makes the rows and fires."""
    one = scheduler(databases[0], "a")
    await admin.execute(REFUSE_LEASES)
    await admin.execute(REFUSE_TRIGGER)
    try:
        with capture_logs() as logs:
            assert await one.run_due() == 0
        assert [log["event"] for log in logs] == ["schedule_leases_failed"]
    finally:
        await admin.execute("DROP FUNCTION platform.refuse_leases() CASCADE")
    assert await one.run_due() == 1
