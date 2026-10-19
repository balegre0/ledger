"""Durable jobs (§3.6, ADR 0003): what a module declares and what its handlers answer.

A module enqueues a job in the unit that decides the work (``uow.jobs.enqueue``); the runtime's runner
claims it, runs its handler outside any unit and records the outcome under the job's fencing token, and
its scheduler enqueues jobs on a cadence, once per slot across replicas. Those engines stay in their
modules. ``maintenance_jobs`` are the purges a module runs over its own schema when no other role can (the
signer's).
"""

from ledger_platform.jobs.maintenance import PURGE_BATCH, maintenance_jobs
from ledger_platform.jobs.outcomes import Done, Job, JobSpec, Park, RunAgain

__all__ = [
    "PURGE_BATCH",
    "Done",
    "Job",
    "JobSpec",
    "Park",
    "RunAgain",
    "maintenance_jobs",
]
