"""Durable jobs (§3.6, ADR 0003): outcomes, a runner with leases and fencing, schedules, purges.

A module enqueues a job in the unit that decides the work (``uow.jobs.enqueue``); a ``JobRunner`` claims it,
runs its handler outside any unit and records the outcome under the job's fencing token. A ``Scheduler``
enqueues jobs on a cadence, once per slot across replicas.
"""

from ledger_platform.jobs.maintenance import (
    DONE_JOBS_KEPT,
    PURGE_DONE_JOBS,
    PURGE_IDEMPOTENCY_KEYS,
    maintenance_jobs,
)
from ledger_platform.jobs.outcomes import Done, Job, JobHandler, JobOutcome, JobSpec, Park, RunAgain
from ledger_platform.jobs.runner import JobRunner, RunnerOptions
from ledger_platform.jobs.schedules import Schedule, Scheduler

__all__ = [
    "DONE_JOBS_KEPT",
    "PURGE_DONE_JOBS",
    "PURGE_IDEMPOTENCY_KEYS",
    "Done",
    "Job",
    "JobHandler",
    "JobOutcome",
    "JobRunner",
    "JobSpec",
    "Park",
    "RunAgain",
    "RunnerOptions",
    "Schedule",
    "Scheduler",
    "maintenance_jobs",
]
