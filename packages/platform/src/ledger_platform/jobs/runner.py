"""The job runner (§3.6, ADR 0003): claim due jobs, run their handlers, record what they answered.

A claim takes due jobs of one kind in a short unit of its own: ``ready`` ones whose ``run_at`` has come and
``running`` ones whose lease ran out (their worker died), ``FOR UPDATE SKIP LOCKED``, so two workers never
take one job. It marks them ``running`` under a lease of the kind's length with a fresh fencing token, and
counts the attempt then, so a worker that dies mid-run still spends it: a job whose lease ran out after its
last attempt is not claimed again but made ``dead`` (``worker_lost``), and a crash loop ends.

The handler runs outside any unit, as its job (``acting_as``) and in the trace that enqueued it. Its outcome
is recorded in another unit by an ``UPDATE`` that names the job's fencing token: a worker whose lease was
taken over (a zombie) records nothing, logs ``job_lease_lost`` and counts it. ``Done`` finishes the job;
``RunAgain(at)`` makes it ready at ``at`` with its attempts started over (dead when ``at`` is past its
deadline); ``Park(reason)`` waits for a wake. An error of kind ``INVALID`` or ``CONFLICT``, a failure of the
last attempt or one past the deadline makes the job dead; any other error makes it ready again after the
kind's backoff. Dead jobs are kept for an operator.

Time is the database's: due and lease comparisons are ``now()`` in SQL, and the times a record writes are the
recording unit's ``now``.
"""

import asyncio
from contextlib import nullcontext
from functools import partial
from types import MappingProxyType
from typing import ClassVar, Final, Never, NotRequired, TypedDict, Unpack, assert_type, final
from uuid import UUID
lazy from collections.abc import Callable, Mapping, Sequence
lazy from contextlib import AbstractContextManager
lazy from datetime import datetime

import structlog
from prometheus_client import Counter
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select, update
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import ColumnElement, RowMapping, Update

from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.codec import JsonValue
from ledger_platform.data.queue import DEFAULT_BACKOFF, claim
from ledger_platform.data.scopes import scope_of
from ledger_platform.jobs.outcomes import Done, Job, Park, RunAgain
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.data.queue import Backoff
lazy from ledger_platform.jobs.outcomes import JobHandler, JobOutcome
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_platform.runtime.manifest import JobDecl

POLL: Final = 1.0
"""Seconds between two claims of an idle runner (Decision 17: each runner polls its own claim)."""
_FINAL: Final = frozenset({ErrorKind.INVALID, ErrorKind.CONFLICT})
"""Error kinds no retry can cure."""
_DEADLINE_PASSED: Final = "deadline_passed"
_WORKER_LOST: Final = "worker_lost"
_logger = structlog.stdlib.get_logger(__name__)

type _Values = dict[str, object]
type _Decision = Callable[[datetime], tuple[_Values, str]]
"""From the recording unit's time: the columns to write and the outcome's metric label."""


class RunnerOptions(TypedDict):
    """How a runner claims and retries, and where it reports; each is optional."""

    batch: NotRequired[int]
    """At most this many jobs of a kind per claim (10)."""
    backoff: NotRequired[Backoff]
    """Waits after failed attempts (``DEFAULT_BACKOFF``)."""
    tracing: NotRequired[Tracing | None]
    """Spans for the handlers, continuing each job's trace; none by default."""
    registry: NotRequired[CollectorRegistry | None]
    """Where the counters are registered; unregistered by default."""
    poll: NotRequired[float]
    """Seconds between two claims of ``run`` (``POLL``)."""


class _Claimed(BaseModel):
    """The columns of a claimed row a handler sees."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    kind: str
    key: str
    organization_id: UUID | None
    company_id: UUID | None
    mode: str | None
    payload: dict[str, JsonValue]
    attempts: int
    max_attempts: int
    deadline_at: AwareDatetime | None
    lease_token: UUID
    traceparent: str | None


def _job_of(row: RowMapping) -> Job:
    claimed = _Claimed.model_validate(dict(row))
    return Job(
        kind=claimed.kind,
        key=claimed.key,
        scope=scope_of(claimed.organization_id, claimed.company_id, claimed.mode),
        payload=MappingProxyType(claimed.payload),
        attempts=claimed.attempts,
        max_attempts=claimed.max_attempts,
        deadline_at=claimed.deadline_at,
        lease_token=claimed.lease_token,
        traceparent=claimed.traceparent,
    )


def _code_of(error: Exception) -> str:
    """The error's stable code, ``internal`` when none names it: rows and logs never hold a message."""
    return error.code if isinstance(error, LedgerError) else "internal"


def _outcome(job: Job, outcome: JobOutcome, now: datetime) -> tuple[_Values, str]:
    """What ``outcome`` writes (see the module)."""
    del now
    match outcome:
        case Done():
            return {"status": "done"}, "done"
        case RunAgain(at=at) if job.deadline_at is not None and at > job.deadline_at:
            return {"status": "dead", "last_error_code": _DEADLINE_PASSED}, "dead"
        case RunAgain(at=at):
            return {"status": "ready", "run_at": at, "attempts": 0}, "run_again"
        case _:
            assert_type(outcome, Park)
            return {"status": "parked", "parked_reason": outcome.reason}, "parked"


@final
class JobRunner:
    """Runs one module's job kinds (see the module)."""

    __slots__ = ("_backoff", "_batch", "_database", "_finished", "_kinds", "_lease_lost", "_poll", "_tracing")

    def __init__(
        self,
        *,
        database: BoundDatabase,
        kinds: Mapping[str, tuple[JobDecl, JobHandler]],
        **options: Unpack[RunnerOptions],
    ) -> None:
        registry = options.get("registry")
        self._database = database
        self._kinds = MappingProxyType(dict(kinds))
        self._batch = options.get("batch", 10)
        self._backoff = options.get("backoff", DEFAULT_BACKOFF)
        self._tracing = options.get("tracing")
        self._poll = options.get("poll", POLL)
        self._lease_lost = Counter(
            "ledger_job_lease_lost",
            "Job outcomes not recorded: another worker had taken the job over after its lease ran out.",
            ["kind"],
            registry=registry,
        )
        self._finished = Counter(
            "ledger_jobs_finished",
            "Job runs recorded, by outcome (done, run_again, parked, retry, dead).",
            ["kind", "outcome"],
            registry=registry,
        )

    async def claim_now(self) -> Sequence[Job]:
        """Claim up to ``batch`` due jobs of every kind, each kind in a short unit of its own."""
        claimed: list[Job] = []
        for kind, (decl, _) in self._kinds.items():
            claimed += await self._claim(kind, decl, self._batch)
        return claimed

    async def handle(self, job: Job) -> None:
        """Run ``job``'s handler as the job, then record its outcome under the job's fencing token."""
        _, handler = self._kinds[job.kind]
        with acting_as(Actor(f"system:job:{job.kind}", f"{job.kind}:{job.key}")):
            try:
                with self._span(job):
                    outcome = await handler(job)
            except Exception as error:  # a handler may fail with anything; a cancellation is no Exception
                _logger.exception("job_failed", kind=job.kind, code=_code_of(error))
                await self._record(job, partial(self._failure, job, error))
                return
        await self._record(job, partial(_outcome, job, outcome))

    async def run_due(self) -> int:
        """One pass: claim what is due now and handle it, one job after the other; how many were claimed.

        A job that answers ``RunAgain`` with a time already come is due again at the next pass, not this one.
        """
        jobs = await self.claim_now()
        for job in jobs:
            await self.handle(job)
        return len(jobs)

    async def run(self) -> Never:
        """Claim and handle forever, never more jobs of a kind at once than its ``concurrency``.

        Each poll claims, per kind, at most the slots that kind has free; each job runs in a task of its
        own. A failure to record (the database is gone) ends the run, for its supervisor to restart.
        """
        held = dict.fromkeys(self._kinds, 0)
        async with asyncio.TaskGroup() as group:
            while True:
                for kind, (decl, _) in self._kinds.items():
                    free = min(self._batch, decl.concurrency - held[kind])
                    if free > 0:
                        for job in await self._claim(kind, decl, free):
                            held[kind] += 1
                            group.create_task(self._hold(job, held))
                await asyncio.sleep(self._poll)

    async def _hold(self, job: Job, held: dict[str, int]) -> None:
        try:
            await self.handle(job)
        finally:
            held[job.kind] -= 1

    async def _claim(self, kind: str, decl: JobDecl, limit: int) -> list[Job]:
        jobs = self._database.tables.jobs
        expired = and_(jobs.c.kind == kind, jobs.c.status == "running", jobs.c.lease_until < func.now())
        async with self._database.unit(None) as unit:
            lost = (await unit.connection.execute(_bury(jobs, expired))).rowcount
            rows = await claim(
                unit.connection,
                jobs,
                keys=("kind", "key"),
                due=or_(
                    and_(jobs.c.kind == kind, jobs.c.status == "ready", jobs.c.run_at <= func.now()), expired
                ),
                order_by=(jobs.c.run_at,),
                limit=limit,
                lease=decl.lease,
                extra={"updated_at": func.now()},
            )
        if lost:
            _logger.warning("job_worker_lost", kind=kind, jobs=lost)
            self._finished.labels(kind=kind, outcome="dead").inc(lost)
        return [_job_of(row) for row in rows]

    def _span(self, job: Job) -> AbstractContextManager[object]:
        tracing = self._tracing
        if tracing is None:
            return nullcontext()
        return tracing.consuming(f"job {job.kind}", job.traceparent)

    def _failure(self, job: Job, error: Exception, now: datetime) -> tuple[_Values, str]:
        """What a failed run writes: dead when no retry can help, else ready after the backoff."""
        code = _code_of(error)
        hopeless = isinstance(error, LedgerError) and error.kind in _FINAL
        past_deadline = job.deadline_at is not None and now >= job.deadline_at
        if hopeless or job.attempts >= job.max_attempts or past_deadline:
            return {"status": "dead", "last_error_code": code}, "dead"
        return {
            "status": "ready",
            "run_at": now + self._backoff.after(job.attempts),
            "last_error_code": code,
        }, "retry"

    async def _record(self, job: Job, decide: _Decision) -> None:
        jobs = self._database.tables.jobs
        async with self._database.unit(None) as unit:
            values, outcome = decide(unit.now)
            recorded = await unit.connection.execute(
                update(jobs)
                .where(
                    jobs.c.kind == job.kind,
                    jobs.c.key == job.key,
                    jobs.c.lease_token == job.lease_token,
                    jobs.c.status == "running",
                )
                .values(lease_until=None, lease_token=None, updated_at=unit.now, **values)
                .returning(jobs.c.key)
            )
            kept = recorded.first() is not None
        if kept:
            self._finished.labels(kind=job.kind, outcome=outcome).inc()
        else:
            _logger.warning("job_lease_lost", kind=job.kind)
            self._lease_lost.labels(kind=job.kind).inc()


def _bury(jobs: AnyTable, expired: ColumnElement[bool]) -> Update:
    """Expired jobs whose last attempt was spent: dead (``worker_lost``), never claimed again."""
    spent = (
        select(jobs.c.kind, jobs.c.key)
        .where(expired, jobs.c.attempts >= jobs.c.max_attempts)
        .with_for_update(skip_locked=True)
        .cte("spent")
    )
    return (
        update(jobs)
        .where(jobs.c.kind == spent.c.kind, jobs.c.key == spent.c.key)
        .values(
            status="dead",
            last_error_code=_WORKER_LOST,
            lease_until=None,
            lease_token=None,
            updated_at=func.now(),
        )
    )
