"""The claim and the recovery read through their partial indexes, never sorting the due set (I5).

On 100 000 due jobs and inbox rows (and 100 000 running ones whose leases ran out), ``EXPLAIN`` of each
statement the runners send: an index scan of ``*_due`` (or ``*_expired``) under a ``Limit``, no sort and no
sequential scan.
"""

from datetime import timedelta
from typing import Final
lazy from collections.abc import AsyncGenerator, Iterator

import psycopg
import pytest
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import and_, create_engine, func
lazy from sqlalchemy import Delete, Update

from ledger_platform.data.queue import DEFAULT_BACKOFF, claim_statement, recovery_statement
from ledger_platform.data.tables import chassis_tables
from ledger_platform.events.consumers import Consumer
from ledger_platform.events.inbox import claimable
from ledger_platform.jobs.maintenance import purge_done_jobs_statement, purge_expired_keys_statement
from ledger_platform.jobs.runner import bury_statement
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import RawEvent
lazy from ledger_testing.postgres import WorkerDatabase

FILL: Final = (
    """
    INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at, lease_until)
    SELECT 'probe.export', 'k' || n, CASE WHEN n % 2 = 0 THEN 'ready' ELSE 'running' END,
           now() - interval '1 hour', 10, now(), now(),
           CASE WHEN n % 2 = 0 THEN NULL ELSE now() - interval '1 hour' END
    FROM generate_series(1, 200000) AS n
    """,
    """
    INSERT INTO other.inbox (consumer, event_id, type, version, source, occurred_at, payload, status,
                             next_attempt_at, lease_until)
    SELECT 'other.renames', gen_random_uuid(), 'probe.thing.renamed', 1, 'probe', now(), '{}',
           CASE WHEN n % 2 = 0 THEN 'ready' ELSE 'running' END, now() - interval '1 hour',
           CASE WHEN n % 2 = 0 THEN NULL ELSE now() - interval '1 hour' END
    FROM generate_series(1, 200000) AS n
    """,
    """
    INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at, deadline_at)
    SELECT 'probe.export', 'd' || n, 'ready', now() + interval '1 hour', 10, now(), now(),
           now() + interval '1 day'
    FROM generate_series(1, 50000) AS n
    """,
    """
    INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at)
    SELECT 'probe.export', 'x' || n, 'done', now() - interval '30 days', 10, now(),
           now() - interval '30 days' + n * interval '1 second'
    FROM generate_series(1, 100000) AS n
    """,
    """
    INSERT INTO probe.idempotency_keys (organization_id, mode, principal, route, key, request_hash,
                                        created_at, expires_at)
    SELECT gen_random_uuid(), 'test', 'p', 'POST /v1/things', 'k' || n, '\\x00', now(),
           now() - interval '1 day' + n * interval '1 second'
    FROM generate_series(1, 100000) AS n
    """,
    "ANALYZE probe.jobs",
    "ANALYZE probe.idempotency_keys",
    "ANALYZE other.inbox",
)
_PLAN: Final = TypeAdapter(list[dict[str, JsonValue]])
_EXPLAINED: Final = TypeAdapter(list[tuple[JsonValue]])
_DIALECT: Final = create_engine("postgresql+psycopg://").dialect
"""How the runners' statements are rendered (no connection is made)."""


@pytest.fixture
async def filled(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session on a database with the rows above (``chassis`` empties them after the test)."""
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        for statement in FILL:
            await connection.execute(statement.encode())
        yield connection


def nodes(node: dict[str, JsonValue]) -> Iterator[dict[str, JsonValue]]:
    """``node`` and every node under it."""
    yield node
    children = node.get("Plans", [])
    assert isinstance(children, list)
    for child in children:
        assert isinstance(child, dict)
        yield from nodes(child)


async def plan(
    connection: psycopg.AsyncConnection, statement: Update | Delete, *, analyze: bool = False
) -> list[dict[str, JsonValue]]:
    compiled = statement.compile(dialect=_DIALECT, compile_kwargs={"render_postcompile": True})
    options = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
    cursor = await connection.execute(f"EXPLAIN ({options}) {compiled.string}".encode(), compiled.params)
    ((found,),) = _EXPLAINED.validate_python(await cursor.fetchall())
    (top,) = _PLAN.validate_python(found)
    root = top["Plan"]
    assert isinstance(root, dict)
    return list(nodes(root))


def assert_reads_the_index_under_a_limit(found: list[dict[str, JsonValue]], index: str) -> None:
    kinds = [node["Node Type"] for node in found]
    assert "Sort" not in kinds
    assert "Incremental Sort" not in kinds
    assert "Seq Scan" not in kinds
    assert "Limit" in kinds
    assert index in [node.get("Index Name") for node in found]


async def test_the_jobs_claim_and_recovery_read_their_partial_indexes(
    filled: psycopg.AsyncConnection,
) -> None:
    jobs = chassis_tables("probe").jobs
    due = and_(jobs.c.kind == "probe.export", jobs.c.status == "ready", jobs.c.run_at <= func.now())
    claimed = claim_statement(
        jobs, keys=("kind", "key"), due=due, order_by=(jobs.c.run_at,), limit=10, lease=timedelta(minutes=1)
    )
    assert_reads_the_index_under_a_limit(await plan(filled, claimed), "jobs_due")
    recovered = recovery_statement(
        jobs,
        keys=("kind", "key"),
        group=jobs.c.kind == "probe.export",
        due_column="run_at",
        max_attempts=jobs.c.max_attempts,
        backoff=DEFAULT_BACKOFF,
    )
    assert_reads_the_index_under_a_limit(await plan(filled, recovered), "jobs_expired")


async def test_the_inbox_claim_and_recovery_read_their_partial_indexes(
    filled: psycopg.AsyncConnection,
) -> None:
    inbox = chassis_tables("other").inbox
    due = and_(
        inbox.c.consumer == "other.renames",
        inbox.c.type.in_(["probe.thing.renamed"]),
        inbox.c.status == "ready",
        inbox.c.next_attempt_at <= func.now(),
    )
    claimed = claim_statement(
        inbox,
        keys=("consumer", "event_id"),
        due=due,
        order_by=(inbox.c.next_attempt_at,),
        limit=20,
        lease=timedelta(minutes=1),
    )
    assert_reads_the_index_under_a_limit(await plan(filled, claimed), "inbox_due")
    recovered = recovery_statement(
        inbox,
        keys=("consumer", "event_id"),
        group=inbox.c.consumer == "other.renames",
        due_column="next_attempt_at",
        max_attempts=10,
        backoff=DEFAULT_BACKOFF,
    )
    assert_reads_the_index_under_a_limit(await plan(filled, recovered), "inbox_expired")


HOT_SUBJECT: Final = (
    """
    INSERT INTO other.inbox (consumer, event_id, type, version, source, subject, aggregate_version,
                             occurred_at, payload, status, next_attempt_at)
    SELECT 'other.ordered', gen_random_uuid(), 'probe.thing.renamed', 1, 'probe',
           '019a0000-0000-7000-8000-000000000000', n, now(), '{}', 'ready', now() - interval '1 hour'
    FROM generate_series(1, 100000) AS n
    """,
    "ANALYZE other.inbox",
)


async def test_an_ordered_claim_on_a_hot_subject_reads_its_heads_once(
    filled: psycopg.AsyncConnection,
) -> None:
    """100 000 waiting events of one subject: the heads come from one pass over ``inbox_head`` (sorted once at
    most), never from a look back per row (which would be quadratic)."""
    for statement in HOT_SUBJECT:
        await filled.execute(statement.encode())
    inbox = chassis_tables("other").inbox
    ordered = Consumer(
        name="other.ordered",
        types=frozenset({"probe.thing.renamed"}),
        max_attempts=10,
        ordered=True,
        handler=never_called,
    )
    claimed = claim_statement(
        inbox,
        keys=("consumer", "event_id"),
        due=claimable(inbox, ordered),
        order_by=(inbox.c.next_attempt_at,),
        limit=20,
        lease=timedelta(minutes=1),
    )
    async with filled.transaction(force_rollback=True):  # ANALYZE runs the claim: it is undone
        found = await plan(filled, claimed, analyze=True)
    kinds = [node["Node Type"] for node in found]
    assert "Seq Scan" not in kinds
    (due,) = [node for node in found if node.get("Index Name") == "inbox_due"]
    assert "hashed SubPlan" in str(due.get("Filter"))  # the heads, computed once and looked up per row
    (heads,) = [node for node in found if node.get("Index Name") == "inbox_head"]
    assert heads["Actual Loops"] == 1  # one pass over the subject's live rows, whatever their number
    claimed_rows = [node for node in found if node["Node Type"] == "ModifyTable"]
    assert [node["Actual Rows"] for node in claimed_rows] == [1]  # the subject's head, and only it


async def never_called(event: RawEvent, unit: UnitOfWork) -> None:
    raise AssertionError((event, unit))


async def test_the_deadline_sweep_reads_the_index_of_jobs_that_have_one(
    filled: psycopg.AsyncConnection,
) -> None:
    """On 350 000 jobs of which 50 000 have a deadline that has not passed, the sweep reads none else."""
    jobs = chassis_tables("probe").jobs
    found = await plan(filled, bury_statement(jobs, "probe.export"))
    assert "jobs_deadline" in [node.get("Index Name") for node in found]
    assert "Seq Scan" not in [node["Node Type"] for node in found]


async def test_the_done_jobs_purge_reads_the_index_of_done_jobs_under_a_limit(
    filled: psycopg.AsyncConnection,
) -> None:
    jobs = chassis_tables("probe").jobs
    found = await plan(filled, purge_done_jobs_statement(jobs, 5000))
    assert_reads_the_index_under_a_limit(found, "jobs_done")


async def test_the_expired_keys_purge_reads_the_expiry_index_under_a_limit(
    filled: psycopg.AsyncConnection,
) -> None:
    keys = chassis_tables("probe").idempotency_keys
    found = await plan(filled, purge_expired_keys_statement(keys, 5000))
    assert_reads_the_index_under_a_limit(found, "idempotency_expiry")
