"""``claim`` on real PostgreSQL: skip-locked claims, leases, fencing tokens, attempts counted (§3.6)."""

import asyncio
from datetime import timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator, Sequence

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import and_, or_
lazy from sqlalchemy import RowMapping

from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.queue import claim
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_testing.database import app_url
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.postgres import WorkerDatabase

KIND: Final = "probe.export"
LEASE: Final = timedelta(seconds=60)
_CLAIMED: Final = TypeAdapter(tuple[str, str, int, object, object])


class Silent:
    def signal(self, channel: object) -> None:
        del channel


@pytest.fixture
async def probe(chassis: WorkerDatabase, clock: ManualClock) -> AsyncGenerator[BoundDatabase]:
    database = Database.create(app_url(chassis), pool_size=2)
    try:
        yield BoundDatabase(
            database,
            schema="probe",
            source="probe",
            jobs={KIND: JobPolicy(max_attempts=5, deadline=None)},
            signals=Silent(),
            clock=clock,
        )
    finally:
        await database.aclose()


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


async def claim_due(unit: UnitOfWork, limit: int) -> Sequence[RowMapping]:
    """Ready jobs that are due, and running ones whose lease ran out, oldest first."""
    jobs = unit.tables.jobs
    due = or_(
        and_(jobs.c.status == "ready", jobs.c.run_at <= unit.now),
        and_(jobs.c.status == "running", jobs.c.lease_until < unit.now),
    )
    return await claim(
        unit.connection,
        jobs,
        keys=("kind", "key"),
        due=due,
        order_by=(jobs.c.run_at, jobs.c.key),
        limit=limit,
        now=unit.now,
        lease=LEASE,
        extra={"updated_at": unit.now},
    )


def shown(rows: Sequence[RowMapping]) -> list[tuple[str, str, int, object, object]]:
    """The claimed rows by key (``RETURNING`` promises no order)."""
    return sorted(
        _CLAIMED.validate_python(
            (row["key"], row["status"], row["attempts"], row["lease_until"], row["updated_at"])
        )
        for row in rows
    )


async def test_two_claims_at_once_never_share_a_row(
    probe: BoundDatabase, clock: ManualClock, admin: psycopg.AsyncConnection
) -> None:
    async with probe.unit(None) as uow:
        for key in ("j1", "j2", "j3"):
            uow.jobs.enqueue(KIND, key)
        uow.jobs.enqueue(KIND, "later", run_at=clock() + timedelta(hours=1))
    now = clock.advance(timedelta(seconds=1))
    first_claimed = asyncio.Event()
    release = asyncio.Event()

    async def first() -> Sequence[RowMapping]:
        async with probe.unit(None) as uow:
            claimed = await claim_due(uow, 2)
            first_claimed.set()
            await release.wait()  # holds its row locks while the second claims
            return claimed

    holding = asyncio.create_task(first())
    await first_claimed.wait()
    async with probe.unit(None) as uow:
        second = await claim_due(uow, 5)
    release.set()
    lease_until = now + LEASE
    assert shown(await holding) == [
        ("j1", "running", 1, lease_until, now),
        ("j2", "running", 1, lease_until, now),
    ]
    assert shown(second) == [("j3", "running", 1, lease_until, now)]
    cursor = await admin.execute(
        "SELECT count(DISTINCT lease_token) FROM probe.jobs WHERE status = 'running'"
    )
    assert await cursor.fetchall() == [(3,)]


async def test_an_expired_lease_is_claimed_again_with_a_new_token_and_one_more_attempt(
    probe: BoundDatabase, clock: ManualClock
) -> None:
    async with probe.unit(None) as uow:
        uow.jobs.enqueue(KIND, "j1")
    async with probe.unit(None) as uow:
        (claimed,) = await claim_due(uow, 1)
    async with probe.unit(None) as uow:
        assert await claim_due(uow, 1) == []  # leased
    later = clock.advance(LEASE + timedelta(seconds=1))
    async with probe.unit(None) as uow:
        (again,) = await claim_due(uow, 1)
    assert shown([again]) == [("j1", "running", 2, later + LEASE, later)]
    assert again["lease_token"] != claimed["lease_token"]
