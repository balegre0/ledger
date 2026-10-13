"""The platform's own jobs (§6.4; Decision 9): purge expired idempotency keys and old done jobs, every schema.

Both delete with a plain ``DELETE``, never ``RETURNING`` what they remove: an idempotency key holds a stored
response. Expired keys are deleted in each schema's ``purge`` role (``BoundDatabase.purging``), which sees
expired keys of every organization and nothing else; done jobs (outside row-level security) in a unit
without a scope. Dead jobs are kept until an operator acts. Times are the database's.
"""

from datetime import timedelta
from typing import Final
lazy from collections.abc import Sequence

from sqlalchemy import delete, func

from ledger_platform.jobs.outcomes import Done, JobSpec
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome

PURGE_IDEMPOTENCY_KEYS: Final = "platform.purge_idempotency_keys"
PURGE_DONE_JOBS: Final = "platform.purge_done_jobs"
DONE_JOBS_KEPT: Final = timedelta(days=7)


def maintenance_jobs(databases: Sequence[BoundDatabase]) -> tuple[JobSpec, JobSpec]:
    """The two purge jobs over every bound schema (the platform's assembly hands them to its runner)."""
    bound = tuple(databases)

    async def purge_keys(job: Job) -> JobOutcome:
        del job
        for database in bound:
            keys = database.tables.idempotency_keys
            async with database.purging() as connection:
                await connection.execute(delete(keys).where(keys.c.expires_at < func.now()))
        return Done()

    async def purge_jobs(job: Job) -> JobOutcome:
        del job
        for database in bound:
            jobs = database.tables.jobs
            async with database.unit(None) as unit:
                await unit.connection.execute(
                    delete(jobs).where(
                        jobs.c.status == "done", jobs.c.updated_at < func.now() - DONE_JOBS_KEPT
                    )
                )
        return Done()

    return JobSpec(kind=PURGE_IDEMPOTENCY_KEYS, handler=purge_keys), JobSpec(
        kind=PURGE_DONE_JOBS, handler=purge_jobs
    )
