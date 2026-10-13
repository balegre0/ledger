"""``claim`` on real PostgreSQL: skip-locked claims, leases, fencing tokens, attempts counted (§3.6).

Due rows and leases are measured on the database's clock (``now()``), the one every worker shares (I3).
"""

import asyncio
from datetime import timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator, Sequence

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import and_, func, or_
lazy from sqlalchemy import RowMapping

from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.queue import claim
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_testing.postgres import WorkerDatabase

KIND: Final = "probe.export"
LEASE: Final = timedelta(seconds=60)
_CLAIMED: Final = TypeAdapter(tuple[str, str, int, object, object])


class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@pytest.fixture
async def probe(chassis: WorkerDatabase) -> AsyncGenerator[BoundDatabase]:
    database = Database.create(app_url(chassis), pool_size=2)
    try:
        yield BoundDatabase(
            database,
            schema="probe",
            source="probe",
            jobs={KIND: JobPolicy(max_attempts=5, deadline=None)},
            signals=Silent(),
        )
    finally:
        await database.aclose()


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


async def claim_due(unit: UnitOfWork, limit: int) -> Sequence[RowMapping]:
    """Ready jobs that are due and running ones whose lease ran out, oldest first, on the database's clock."""
    jobs = unit.tables.jobs
    now = func.now()
    due = or_(
        and_(jobs.c.status == "ready", jobs.c.run_at <= now),
        and_(jobs.c.status == "running", jobs.c.lease_until < now),
    )
    return await claim(
        unit.connection,
        jobs,
        keys=("kind", "key"),
        due=due,
        order_by=(jobs.c.run_at, jobs.c.key),
        limit=limit,
        lease=LEASE,
        extra={"updated_at": now},
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
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with probe.unit(None) as uow:
        for key in ("j1", "j2", "j3"):
            uow.jobs.enqueue(KIND, key)
        uow.jobs.enqueue(KIND, "later", run_at=uow.now + timedelta(hours=1))
    first_claimed = asyncio.Event()
    release = asyncio.Event()

    async def first() -> tuple[Sequence[RowMapping], UnitOfWork]:
        async with probe.unit(None) as uow:
            claimed = await claim_due(uow, 2)
            first_claimed.set()
            await release.wait()  # holds its row locks while the second claims
            return claimed, uow

    holding = asyncio.create_task(first())
    await first_claimed.wait()
    async with probe.unit(None) as second_unit:
        second = await claim_due(second_unit, 5)
    release.set()
    held, first_unit = await holding
    at, then = first_unit.now, second_unit.now
    assert shown(held) == [("j1", "running", 1, at + LEASE, at), ("j2", "running", 1, at + LEASE, at)]
    assert shown(second) == [("j3", "running", 1, then + LEASE, then)]
    cursor = await admin.execute(
        "SELECT count(DISTINCT lease_token) FROM probe.jobs WHERE status = 'running'"
    )
    assert await cursor.fetchall() == [(3,)]


async def test_an_expired_lease_is_claimed_again_with_a_new_token_and_one_more_attempt(
    probe: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with probe.unit(None) as uow:
        uow.jobs.enqueue(KIND, "j1")
    async with probe.unit(None) as uow:
        (claimed,) = await claim_due(uow, 1)
    async with probe.unit(None) as uow:
        assert await claim_due(uow, 1) == []  # leased
    await admin.execute("UPDATE probe.jobs SET lease_until = now() - interval '1 second'")
    async with probe.unit(None) as uow:
        (again,) = await claim_due(uow, 1)
    assert shown([again]) == [("j1", "running", 2, uow.now + LEASE, uow.now)]
    assert again["lease_token"] != claimed["lease_token"]
