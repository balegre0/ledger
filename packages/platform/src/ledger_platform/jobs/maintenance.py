"""The platform's own jobs (§6.4; Decision 9): purge expired idempotency keys and old done jobs, every schema.

Both delete with a plain ``DELETE``, never ``RETURNING`` what they remove: an idempotency key holds a stored
response. Expired keys are deleted in each schema's ``purge`` role (``BoundDatabase.purging``), which sees
expired keys of every organization and nothing else; done jobs (outside row-level security) in a unit
without a scope. Dead jobs are kept until an operator acts. Times are the database's.

They delete ``PURGE_BATCH`` rows at a time, each batch in a transaction of its own, until one comes back
short. One statement over a table that has grown for a week would outlast the statement timeout, roll back
and leave the table bigger at every retry.
"""

from datetime import timedelta
from typing import Final
lazy from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import Text, any_, delete, func, literal_column, select
lazy from sqlalchemy import Delete

from ledger_platform.config.options import require_at_least
from ledger_platform.jobs.outcomes import Done, JobSpec
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

PURGE_IDEMPOTENCY_KEYS: Final = "platform.purge_idempotency_keys"
PURGE_DONE_JOBS: Final = "platform.purge_done_jobs"
DONE_JOBS_KEPT: Final = timedelta(days=7)
PURGE_BATCH: Final = 5_000

type _Purge = Callable[[BoundDatabase, int], Awaitable[int]]
"""Deletes up to a batch of rows of one schema in a transaction of its own; how many it deleted."""


def purge_expired_keys_statement(keys: AnyTable, batch: int) -> Delete:
    """Delete up to ``batch`` idempotency keys past their expiry, the longest expired first."""
    old = (
        select(_ROW)
        .select_from(keys)
        .where(keys.c.expires_at < func.now())
        .order_by(keys.c.expires_at)
        .limit(batch)
    )
    return delete(keys).where(any_(func.array(old.scalar_subquery())) == _ROW)


async def _expired_keys(database: BoundDatabase, batch: int) -> int:
    async with database.purging() as connection:
        purged = await connection.execute(
            purge_expired_keys_statement(database.tables.idempotency_keys, batch)
        )
    return purged.rowcount


_ROW: Final = literal_column("ctid", type_=Text())
"""A row's physical address: what one statement may delete the rows it has just chosen by, without a join.

Deleting ``WHERE (primary key) IN (SELECT ... LIMIT n)`` lets the planner join the whole table to the batch (a
sequential scan of every row for each batch of a few thousand). Within one statement the chosen addresses
cannot be reused by another row (the snapshot holds them), and a row changed meanwhile is simply not deleted.
"""


def purge_done_jobs_statement(jobs: AnyTable, batch: int) -> Delete:
    """Delete up to ``batch`` jobs done more than ``DONE_JOBS_KEPT`` ago, the oldest of the index's order."""
    old = (
        select(_ROW)
        .select_from(jobs)
        .where(jobs.c.status == "done", jobs.c.updated_at < func.now() - DONE_JOBS_KEPT)
        .order_by(jobs.c.updated_at)
        .limit(batch)
    )
    return delete(jobs).where(any_(func.array(old.scalar_subquery())) == _ROW)


async def _done_jobs(database: BoundDatabase, batch: int) -> int:
    async with database.unit(None) as unit:
        purged = await unit.connection.execute(purge_done_jobs_statement(database.tables.jobs, batch))
    return purged.rowcount


async def _purge_all(purge: _Purge, databases: Sequence[BoundDatabase], batch: int) -> None:
    """Run ``purge`` over each schema until a batch comes back short."""
    for database in databases:
        while await purge(database, batch) >= batch:
            pass


def maintenance_jobs(
    databases: Sequence[BoundDatabase], *, batch: int = PURGE_BATCH
) -> tuple[JobSpec, JobSpec]:
    """The two purge jobs over every bound schema (the platform's assembly hands them to its runner)."""
    require_at_least("purge", "batch", batch)
    bound = tuple(databases)

    async def purge_keys(job: Job) -> JobOutcome:
        del job
        await _purge_all(_expired_keys, bound, batch)
        return Done()

    async def purge_jobs(job: Job) -> JobOutcome:
        del job
        await _purge_all(_done_jobs, bound, batch)
        return Done()

    return JobSpec(kind=PURGE_IDEMPOTENCY_KEYS, handler=purge_keys), JobSpec(
        kind=PURGE_DONE_JOBS, handler=purge_jobs
    )
