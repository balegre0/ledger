"""The job runner (§3.6, ADR 0003): claim due jobs, run their handlers, record what they answered.

A claim works on one kind in a short unit of its own. A due or abandoned job whose deadline has passed is made
``dead`` first (``deadline_passed``): no handler runs past it. Then jobs whose lease ran out (their worker
died) are recovered by the queue's shared rule: ``ready`` again a backoff after the lease ran out, or ``dead``
(``worker_lost``) once their attempts are spent, so a crash loop ends. Then ``ready`` jobs whose ``run_at``
has come are taken ``FOR UPDATE SKIP LOCKED``, so two workers never take one job, and marked ``running`` under
a lease of the kind's length with a fresh fencing token; the attempt is counted then, so a worker that dies
mid-run still spends it.

The handler runs outside any unit, as its job (``acting_as``) and in the trace that enqueued it. Before it
starts, the runner renews the lease once: a job taken over between the claim and the start never runs here
(``job_lease_lost``). While it runs, the runner renews the lease every third of its length under the job's
token; a renewal that finds the lease taken over, or that fails, cancels the handler (``job_lease_lost``): an
unrenewed lease runs out and another worker would run the job too. Its outcome is recorded in another unit by
an ``UPDATE`` that names the job's fencing token: a worker whose lease was taken over (a zombie) records
nothing, logs ``job_lease_lost`` and counts it. ``Done`` finishes the job; ``RunAgain(at)`` makes it ready at
``at`` with its attempts started over (dead when ``at`` is past its deadline); ``Park(reason)`` waits for a
wake. An error of kind ``INVALID`` or ``CONFLICT`` or a failure of the last attempt makes the job dead with
the error's code; a retry that would come after the deadline makes it dead with ``deadline_passed``; any other
error makes it ready again after the kind's backoff. Dead jobs are kept for an operator.

A runner asked to ``stop`` claims nothing more, lets the jobs in flight finish and record, then sets
``drained``. It starts no job after ``stop()``: jobs a claim in flight brings back, and a claimed job whose
handler has not begun, are given back unstarted (``ready``, the attempt refunded, the lease cleared). A runner
cancelled with jobs in flight stops their handlers and gives the jobs back, in a short shielded unit:
``ready`` at once, the lease cleared; a job whose handler never started gets its attempt back.

Time is the database's: due and lease comparisons are ``now()`` in SQL, and the times a record writes are the
recording unit's ``now``.
"""

import asyncio
import contextlib
import math
from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import ClassVar, Final, Never, NotRequired, TypedDict, Unpack, assert_type, final
from uuid import UUID
lazy from collections.abc import Callable, Mapping, Sequence
lazy from contextlib import AbstractContextManager
lazy from datetime import datetime

import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import and_, case, func, or_, select, update
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy import RowMapping, Update

from ledger_kernel.errors import ErrorKind, LedgerError, UnavailableError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.codec import JsonValue
from ledger_platform.data.queue import DEFAULT_BACKOFF, RELEASE_TIMEOUT, claim, parked, recover, release
from ledger_platform.data.scopes import scope_of
from ledger_platform.data.unit_of_work import WOKEN_WHILE_RUNNING
from ledger_platform.jobs.outcomes import Done, Job, Park, RunAgain
from ledger_platform.observability.metrics import counter
lazy from ledger_platform.config.manifest import JobDecl
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import AnyTable
lazy from ledger_platform.data.queue import Backoff
lazy from ledger_platform.jobs.outcomes import JobHandler, JobOutcome
lazy from ledger_platform.observability.tracing import Tracing

POLL: Final = 1.0
"""Seconds between two claims of an idle runner (Decision 17: each runner polls its own claim)."""
_FINAL: Final = frozenset({ErrorKind.INVALID, ErrorKind.CONFLICT})
"""Error kinds no retry can cure."""
_DEADLINE_PASSED: Final = "deadline_passed"
_CLAIM_RETRY_LONGEST: Final = 30.0
"""Seconds a runner waits at most before claiming again after the database refused a claim."""
_CLAIM_RETRY_DOUBLINGS: Final = 16
_CODES: Final = TypeAdapter(list[str])
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


async def _bounded(job: Job, decl: JobDecl, handler: JobHandler) -> JobOutcome:
    """``handler``'s outcome, or ``JobTimedOutError`` when it takes longer than the kind's ``timeout``.

    A ``TimeoutError`` the handler raises itself (a call of its own that timed out) is its own failure, not
    the job's: only the limit this function set is told apart.
    """
    limit = asyncio.timeout(decl.timeout.total_seconds())
    try:
        async with limit:
            return await handler(job)
    except TimeoutError as error:
        if limit.expired():
            raise JobTimedOutError(job.kind) from error
        raise


def _code_of(error: Exception) -> str:
    """The error's stable code, ``internal`` when none names it: rows and logs never hold a message."""
    return error.code if isinstance(error, LedgerError) else "internal"


class JobTimedOutError(UnavailableError):
    """A handler ran past its kind's ``timeout``: the run is stopped and retried, as any retryable failure."""

    code: ClassVar[str] = "job_timed_out"

    def __init__(self, kind: str) -> None:
        super().__init__(f"a {kind} job's handler ran past its timeout")


class _StoppingError(LedgerError):
    """A job not started because its runner is stopping: given back already, nothing to record."""

    code: ClassVar[str] = "job_runner_stopping"

    def __init__(self, kind: str) -> None:
        super().__init__(f"a {kind} job was not started: its runner is stopping")


class LeaseLostError(LedgerError):
    """A running job's lease was taken over, or could not be renewed: its handler is stopped."""

    code: ClassVar[str] = "job_lease_lost"

    def __init__(self, kind: str) -> None:
        super().__init__(f"a {kind} job lost its lease while its handler ran")


def _cancelled_from_outside() -> bool:
    """Whether the current task itself is being cancelled (not only something it awaited)."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


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
@dataclass(slots=True)
class _Holding:
    """A job ``run`` claimed and has not finished with; ``started`` once its handler has begun."""

    job: Job
    started: bool = False


@final
class JobRunner:
    """Runs one module's job kinds (see the module)."""

    __slots__ = (
        "_backoff",
        "_batch",
        "_database",
        "_finished",
        "_holding",
        "_kinds",
        "_lease_lost",
        "_poll",
        "_recovered",
        "_stopping",
        "_tracing",
        "drained",
    )

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
        self._stopping = asyncio.Event()
        self._holding: dict[UUID, _Holding] = {}
        self.drained: Final = asyncio.Event()
        """Set once a stopped runner's jobs in flight have finished."""
        self._lease_lost = counter(
            registry,
            "ledger_job_lease_lost",
            "Job runs stopped or not recorded: the job was taken over, or its lease could not be renewed.",
            ["kind"],
        )
        self._recovered = counter(
            registry,
            "ledger_job_worker_recovered",
            "Jobs whose lease ran out (their worker died), made ready again after the backoff.",
            ["kind"],
        )
        self._finished = counter(
            registry,
            "ledger_jobs_finished",
            "Job runs recorded, by outcome (done, run_again, parked, retry, dead).",
            ["kind", "outcome"],
        )

    async def claim_now(self) -> Sequence[Job]:
        """Claim up to ``batch`` due jobs of every kind, each kind in a short unit of its own."""
        claimed: list[Job] = []
        for kind, (decl, _) in self._kinds.items():
            claimed += await self._claim(kind, decl, self._batch)
        return claimed

    async def handle(self, job: Job) -> None:
        """Run ``job``'s handler as the job, renewing its lease, then record its outcome under its token."""
        decl, handler = self._kinds[job.kind]
        with acting_as(Actor(f"system:job:{job.kind}", f"{job.kind}:{job.key}")):
            try:
                with self._span(job):
                    outcome = await self._leased(job, decl, handler)
            except LeaseLostError:
                self._lost(job)
                return
            except _StoppingError:
                return
            except Exception as error:  # a handler may fail with anything; a cancellation is no Exception
                _logger.exception("job_failed", kind=job.kind, code=_code_of(error))
                await self._record(job, partial(self._failure, job, error))
                return
        await self._record(job, partial(_outcome, job, outcome))

    async def renew(self, job: Job) -> bool:
        """Extend ``job``'s lease by its kind's length from now, if this worker still holds it (its token)."""
        decl, _ = self._kinds[job.kind]
        jobs = self._database.tables.jobs
        async with self._database.unit(None) as unit:
            renewed = await unit.connection.execute(
                update(jobs)
                .where(
                    jobs.c.kind == job.kind,
                    jobs.c.key == job.key,
                    jobs.c.lease_token == job.lease_token,
                    jobs.c.status == "running",
                )
                .values(lease_until=func.statement_timestamp() + decl.lease)
                .returning(jobs.c.key)
            )
            return renewed.first() is not None

    async def run_due(self) -> int:
        """One pass: claim what is due now and handle it, one job after the other; how many were claimed.

        A job that answers ``RunAgain`` with a time already come is due again at the next pass, not this one.
        """
        jobs = await self.claim_now()
        for job in jobs:
            await self.handle(job)
        return len(jobs)

    def stop(self) -> None:
        """Claim nothing more; let the jobs in flight finish, then set ``drained``."""
        self._stopping.set()

    async def run(self) -> Never:
        """Claim and handle until stopped, never more jobs of a kind at once than its ``concurrency``.

        Each pass claims, per kind, at most the slots that kind has free; each job runs in a task of its own.
        A pass that filled a claim while slots stay free is followed by another at once, else by a poll's
        wait. A claim the database refuses is logged and tried again after a growing wait (up to
        ``_CLAIM_RETRY_LONGEST``), leaving the jobs in flight alone. A failure to record (the database is
        gone while a job ends) ends the run, for its supervisor to restart. Stopped (see the module), it sets
        ``drained`` and waits to be cancelled; whatever way it ends, the jobs it still holds are given back.
        """
        held = dict.fromkeys(self._kinds, 0)
        failures = 0
        try:
            async with asyncio.TaskGroup() as group:
                while not self._stopping.is_set():
                    try:
                        more = await self._claim_free(group, held)
                    except Exception:  # the database: retried, never the end of the jobs in flight
                        failures = min(failures + 1, _CLAIM_RETRY_DOUBLINGS)
                        _logger.exception("job_claim_failed", failures=failures)
                        await self._pause(min(math.ldexp(self._poll, failures), _CLAIM_RETRY_LONGEST))
                        continue
                    failures = 0
                    if not more:
                        await self._pause(self._poll)
        finally:
            if self._holding:
                await self._give_back(list(self._holding.values()))
                self._holding.clear()
        self.drained.set()
        await parked()

    async def _pause(self, seconds: float) -> None:
        """Wait ``seconds``, or less when asked to stop."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._stopping.wait()

    async def _give_back(self, holding: Sequence[_Holding]) -> None:
        """Release ``holding``, jobs this runner claimed, in a short shielded unit (see the module)."""
        started: list[tuple[object, ...]] = [
            (h.job.kind, h.job.key, h.job.lease_token) for h in holding if h.started
        ]
        unstarted: list[tuple[object, ...]] = [
            (h.job.kind, h.job.key, h.job.lease_token) for h in holding if not h.started
        ]
        async with asyncio.timeout(RELEASE_TIMEOUT):
            await asyncio.shield(self._released(started, unstarted))
        _logger.info("jobs_released", jobs=len(holding))

    async def _released(
        self, started: Sequence[tuple[object, ...]], unstarted: Sequence[tuple[object, ...]]
    ) -> None:
        jobs = self._database.tables.jobs
        async with self._database.unit(None) as unit:
            await release(unit.connection, jobs, keys=("kind", "key"), started=started, unstarted=unstarted)

    async def _claim_free(self, group: asyncio.TaskGroup, held: dict[str, int]) -> bool:
        """Claim each kind's free slots; whether a kind filled its claim and still has slots free."""
        more = False
        for kind, (decl, _) in self._kinds.items():
            free = min(self._batch, decl.concurrency - held[kind])
            if free > 0:
                jobs = await self._claim(kind, decl, free)
                if self._stopping.is_set():  # stopped while the claim was in flight: start none of them
                    await self._give_back([_Holding(job) for job in jobs])
                    return False
                for job in jobs:
                    held[kind] += 1
                    self._holding[job.lease_token] = _Holding(job)
                    group.create_task(self._hold(job, held))
                more = more or (len(jobs) == free and held[kind] < decl.concurrency)
        return more

    async def _hold(self, job: Job, held: dict[str, int]) -> None:
        try:
            await self.handle(job)
            _ = self._holding.pop(job.lease_token, None)  # finished or given back: nothing left to give back
        finally:
            held[job.kind] -= 1

    async def _leased(self, job: Job, decl: JobDecl, handler: JobHandler) -> JobOutcome:
        """The handler's outcome, its lease renewed every third of its length while it runs.

        A renewal that finds the lease taken, or that fails, cancels the handler: ``LeaseLostError``.
        """
        if not await self._renewed(job):  # taken over since the claim, or unrenewable: never start it
            raise LeaseLostError(job.kind)
        if self._stopping.is_set():  # claimed before ``stop()``, not started: given back unspent
            await self._give_back([_Holding(job)])
            _ = self._holding.pop(job.lease_token, None)
            raise _StoppingError(job.kind)
        holding = self._holding.get(job.lease_token)
        if holding is not None:  # claimed by ``run`` (``run_due`` holds nothing to give back)
            holding.started = True
        work = asyncio.ensure_future(_bounded(job, decl, handler))
        lost = asyncio.Event()
        keeper = asyncio.create_task(self._keep(job, decl, work, lost))
        try:
            return await work
        except asyncio.CancelledError:
            if lost.is_set() and not _cancelled_from_outside():
                raise LeaseLostError(job.kind) from None
            raise
        finally:
            keeper.cancel()
            await asyncio.gather(keeper, return_exceptions=True)

    async def _keep(
        self, job: Job, decl: JobDecl, work: asyncio.Future[JobOutcome], lost: asyncio.Event
    ) -> None:
        interval = decl.lease.total_seconds() / 3
        while True:
            await asyncio.sleep(interval)
            if not await self._renewed(job):
                lost.set()
                work.cancel()
                return

    async def _renewed(self, job: Job) -> bool:
        """``renew``; a failure counts as a lost lease (unrenewed, another worker may take it)."""
        try:
            return await self.renew(job)
        except Exception:  # the database: the lease cannot be vouched for
            _logger.exception("job_lease_renewal_failed", kind=job.kind)
            return False

    async def _claim(self, kind: str, decl: JobDecl, limit: int) -> list[Job]:
        jobs = self._database.tables.jobs
        keys = ("kind", "key")
        async with self._database.unit(None) as unit:
            passed = len(
                _CODES.validate_python(
                    (await unit.connection.execute(bury_statement(jobs, kind))).scalars().all()
                )
            )
            recovered = await recover(
                unit.connection,
                jobs,
                keys=keys,
                group=jobs.c.kind == kind,
                due_column="run_at",
                max_attempts=jobs.c.max_attempts,
                backoff=self._backoff,
                extra={"updated_at": func.now()},
            )
            rows = await claim(
                unit.connection,
                jobs,
                keys=keys,
                due=and_(jobs.c.kind == kind, jobs.c.status == "ready", jobs.c.run_at <= func.now()),
                order_by=(jobs.c.run_at,),
                limit=limit,
                lease=decl.lease,
                extra={"updated_at": func.now()},
            )
        for count, event in ((recovered.dead, "job_worker_lost"), (passed, "job_deadline_passed")):
            if count:
                _logger.warning(event, kind=kind, jobs=count)
                self._finished.labels(kind=kind, outcome="dead").inc(count)
        if recovered.ready:
            _logger.warning("job_worker_recovered", kind=kind, jobs=recovered.ready)
            self._recovered.labels(kind=kind).inc(recovered.ready)
        return [_job_of(row) for row in rows]

    def _span(self, job: Job) -> AbstractContextManager[object]:
        tracing = self._tracing
        if tracing is None:
            return nullcontext()
        return tracing.consuming(f"job {job.kind}", job.traceparent)

    def _failure(self, job: Job, error: Exception, now: datetime) -> tuple[_Values, str]:
        """What a failed run writes: dead when no retry can help or one would run past the deadline."""
        code = _code_of(error)
        hopeless = isinstance(error, LedgerError) and error.kind in _FINAL
        if hopeless or job.attempts >= job.max_attempts:
            return {"status": "dead", "last_error_code": code}, "dead"
        retry_at = now + self._backoff.after(job.attempts)
        if job.deadline_at is not None and retry_at > job.deadline_at:
            return {"status": "dead", "last_error_code": _DEADLINE_PASSED}, "dead"
        return {"status": "ready", "run_at": retry_at, "last_error_code": code}, "retry"

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
                .values(
                    lease_until=None,
                    lease_token=None,
                    updated_at=unit.now,
                    **_settled(jobs, values, unit.now),
                )
                .returning(jobs.c.key)
            )
            kept = recorded.first() is not None
        if kept:
            self._finished.labels(kind=job.kind, outcome=outcome).inc()
        else:
            self._lost(job)

    def _lost(self, job: Job) -> None:
        _logger.warning("job_lease_lost", kind=job.kind)
        self._lease_lost.labels(kind=job.kind).inc()


def _settled(jobs: AnyTable, values: _Values, now: datetime) -> _Values:
    """``values`` as written: a ``Park`` after a wake that arrived while the handler ran is ``ready`` at once.

    Nothing parked yet could have been woken, so without this the job would wait for a wake that has passed.
    Any other outcome clears the mark a wake left.
    """
    if values["status"] != "parked":
        return {"parked_reason": None, **values}
    woken = jobs.c.parked_reason == WOKEN_WHILE_RUNNING
    return {
        "status": case((woken, "ready"), else_="parked"),
        "parked_reason": case((woken, None), else_=values["parked_reason"]),
        "run_at": case((woken, now), else_=jobs.c.run_at),
    }


def bury_statement(jobs: AnyTable, kind: str) -> Update:
    """Jobs of ``kind`` whose deadline has passed, due or abandoned: dead (``deadline_passed``), their codes.

    A handler would run past the deadline. Jobs whose lease ran out otherwise are the queue's (``recover``).
    """
    now = func.now()
    due = and_(jobs.c.status == "ready", jobs.c.run_at <= now)
    expired = and_(jobs.c.status == "running", jobs.c.lease_until < now)
    doomed = (
        select(jobs.c.kind, jobs.c.key)
        .where(jobs.c.kind == kind, or_(due, expired), jobs.c.deadline_at < now)
        .with_for_update(skip_locked=True)
        .cte("doomed")
    )
    return (
        update(jobs)
        .where(jobs.c.kind == doomed.c.kind, jobs.c.key == doomed.c.key)
        .values(
            status="dead",
            last_error_code=_DEADLINE_PASSED,
            lease_until=None,
            lease_token=None,
            updated_at=func.now(),
        )
        .returning(jobs.c.last_error_code)
    )
