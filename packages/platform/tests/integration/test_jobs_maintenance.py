"""The platform's purges on real PostgreSQL, run as jobs by a ``JobRunner`` of the ``platform`` schema (§6.4).

Every statement the pool sends is recorded: a purge deletes with a plain ``DELETE``, never ``RETURNING`` the
stored responses.
"""

import uuid
from datetime import timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator, Sequence

import psycopg
import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import event

from ledger_kernel.ids import OrganizationId
from ledger_platform.config.manifest import JobDecl
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.jobs.maintenance import (
    DONE_JOBS_KEPT,
    PURGE_DONE_JOBS,
    PURGE_IDEMPOTENCY_KEYS,
    maintenance_jobs,
)
from ledger_platform.jobs.runner import JobRunner
from ledger_testing.database import app_url
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.postgres import WorkerDatabase

A, B = OrganizationId(uuid.uuid7()), OrganizationId(uuid.uuid7())
SCHEMAS: Final = ("probe", "other")
KEY: Final = r"""
    INSERT INTO {schema}.idempotency_keys
        (organization_id, mode, principal, route, key, request_hash, response_body, created_at, expires_at)
    VALUES (%s, 'live', 'user:1', 'POST /v1/things', %s, '\x00', 'stored', now(), now() + %s::interval)
"""
KEYS: Final = {
    "probe": "SELECT key FROM probe.idempotency_keys",
    "other": "SELECT key FROM other.idempotency_keys",
}
JOB: Final = """
    INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at)
    VALUES ('probe.export', %s, %s, now(), 3, now(), now() - %s::interval)
"""


class Silent:
    def signal(self, channel: Channel) -> None:
        del channel


@pytest.fixture
async def database(chassis: WorkerDatabase) -> AsyncGenerator[Database]:
    opened = Database.create(app_url(chassis), pool_size=2)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
async def admin(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture
def statements(database: Database) -> list[str]:
    """Every statement the pool sends, in order."""
    sent: list[str] = []

    def record(*arguments: object) -> None:
        """``before_cursor_execute``: connection, cursor, statement, parameters, context, executemany."""
        sent.append(str(arguments[2]))

    event.listen(database.engine.sync_engine, "before_cursor_execute", record)
    return sent


async def purge(database: Database, kind: str, *, batch: int | None = None) -> int:
    """Enqueue ``kind`` in ``platform`` and let the platform's runner run it; how many jobs it ran."""
    bound = [BoundDatabase(database, schema=schema, source=schema, signals=Silent()) for schema in SCHEMAS]
    policies = {
        kind: JobPolicy(max_attempts=3, deadline=None) for kind in (PURGE_IDEMPOTENCY_KEYS, PURGE_DONE_JOBS)
    }
    platform = BoundDatabase(database, schema="platform", source="platform", jobs=policies, signals=Silent())
    specs = (
        maintenance_jobs([*bound, platform])
        if batch is None
        else maintenance_jobs([*bound, platform], batch=batch)
    )
    kinds = {spec.kind: (JobDecl(kind=spec.kind), spec.handler) for spec in specs}
    async with platform.unit(None) as uow:
        uow.jobs.enqueue(kind, "once")
    return await JobRunner(database=platform, kinds=kinds, registry=CollectorRegistry()).run_due()


async def table(admin: psycopg.AsyncConnection, query: str) -> Sequence[tuple[object, ...]]:
    cursor = await admin.execute(query.encode())
    return [tuple(row) for row in await cursor.fetchall()]


def deletes(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.lstrip().upper().startswith("DELETE")]


async def test_expired_idempotency_keys_of_every_organization_are_purged(
    database: Database, admin: psycopg.AsyncConnection, statements: list[str]
) -> None:
    for schema in SCHEMAS:
        for organization, key, expires in (
            (A, "a-old", "-1 day"),
            (B, "b-old", "-1 hour"),
            (B, "b-live", "1 day"),
        ):
            await admin.execute(KEY.format(schema=schema).encode(), (organization, key, expires))
    assert await purge(database, PURGE_IDEMPOTENCY_KEYS) == 1
    for schema in SCHEMAS:
        assert await table(admin, KEYS[schema]) == [("b-live",)]
    assert await table(admin, "SELECT status FROM platform.jobs") == [("done",)]
    purges = deletes(statements)
    assert len(purges) == 3  # probe, other and platform
    assert not any("RETURNING" in statement.upper() for statement in purges)


async def test_done_jobs_are_purged_after_seven_days_and_dead_ones_kept(
    database: Database, admin: psycopg.AsyncConnection, statements: list[str]
) -> None:
    kept = DONE_JOBS_KEPT - timedelta(days=1)
    for key, status, age in (
        ("old", "done", "8 days"),
        ("recent", "done", f"{kept.days} days"),
        ("dead", "dead", "30 days"),
    ):
        await admin.execute(JOB.encode(), (key, status, age))
    assert await purge(database, PURGE_DONE_JOBS) == 1
    assert await table(admin, "SELECT key, status FROM probe.jobs ORDER BY key") == [
        ("dead", "dead"),
        ("recent", "done"),
    ]
    purges = deletes(statements)
    assert len(purges) == 3
    assert not any("RETURNING" in statement.upper() for statement in purges)


async def test_expired_keys_are_purged_in_short_batches(
    database: Database, admin: psycopg.AsyncConnection, statements: list[str]
) -> None:
    """A table with millions of expired rows must not be one statement under the statement timeout."""
    for number in range(7):
        await admin.execute(KEY.format(schema="probe").encode(), (A, f"old-{number}", "-1 day"))
    await admin.execute(KEY.format(schema="probe").encode(), (A, "live", "1 day"))
    assert await purge(database, PURGE_IDEMPOTENCY_KEYS, batch=3) == 1
    assert await table(admin, KEYS["probe"]) == [("live",)]
    assert len(deletes(statements)) == 3 + 1 + 1  # probe: 3, 3 and 1 rows; other and platform: none


async def test_done_jobs_are_purged_in_short_batches(
    database: Database, admin: psycopg.AsyncConnection, statements: list[str]
) -> None:
    for number in range(6):
        await admin.execute(JOB.encode(), (f"old-{number}", "done", "8 days"))
    await admin.execute(JOB.encode(), ("recent", "done", "1 day"))
    assert await purge(database, PURGE_DONE_JOBS, batch=3) == 1
    assert await table(admin, "SELECT key FROM probe.jobs") == [("recent",)]
    assert len(deletes(statements)) == 3 + 1 + 1  # probe: 3, 3, then none left; other and platform: none


async def test_maintenance_jobs_take_their_kinds(database: Database, admin: psycopg.AsyncConnection) -> None:
    """A module running its own purges (the signer's, Decision 11) names them; they purge its schema alone."""
    kinds = ("probe.purge_idempotency_keys", "probe.purge_done_jobs")
    policies = {kind: JobPolicy(max_attempts=3, deadline=None) for kind in kinds}
    probe = BoundDatabase(database, schema="probe", source="probe", jobs=policies, signals=Silent())
    specs = maintenance_jobs([probe], kinds=kinds)
    assert tuple(spec.kind for spec in specs) == kinds
    for schema in SCHEMAS:
        await admin.execute(KEY.format(schema=schema).encode(), (A, "a-old", "-1 day"))
    async with probe.unit(None) as uow:
        uow.jobs.enqueue(kinds[0], "once")
    handlers = {spec.kind: (JobDecl(kind=spec.kind), spec.handler) for spec in specs}
    assert await JobRunner(database=probe, kinds=handlers, registry=CollectorRegistry()).run_due() == 1
    assert await table(admin, KEYS["probe"]) == []
    assert await table(admin, KEYS["other"]) == [("a-old",)]
    assert await table(admin, "SELECT kind, status FROM probe.jobs") == [(kinds[0], "done")]


@pytest.mark.parametrize("batch", [0, -1])
def test_a_batch_below_one_is_refused(batch: int) -> None:
    with pytest.raises(InvalidOptionError):
        maintenance_jobs([], batch=batch)
