"""What a job handler answers (§3.6), and the job as it receives it."""

from dataclasses import dataclass
from typing import final
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import InvalidValueError
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.codec import JsonValue


@final
@dataclass(frozen=True, slots=True)
class Done:
    """The work is finished; the row is purged after ``DONE_JOBS_KEPT``."""


@final
@dataclass(frozen=True, slots=True)
class RunAgain:
    """Poll again at ``at``: not a failure, attempts start over (a time past the deadline makes it dead)."""

    at: datetime

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise InvalidValueError(field="run_again.at", requirement="an aware datetime")


@final
@dataclass(frozen=True, slots=True)
class Park:
    """Wait for an outside fact; ``uow.jobs.wake`` or ``wake_all`` with ``reason`` makes the job ready."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise InvalidValueError(field="park.reason", requirement="a reason a wake can name")


type JobOutcome = Done | RunAgain | Park


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    """A claimed job, as its handler receives it.

    ``attempts`` counts this run (attempts are counted on claim); ``lease_token`` is the fencing token the
    runner records the outcome with; ``scope`` is the tenant the job was enqueued for, the scope of the unit
    the handler opens for business data (jobs themselves are outside row-level security).
    """

    kind: str
    key: str
    scope: Scope | None
    payload: Mapping[str, JsonValue]
    attempts: int
    max_attempts: int
    deadline_at: datetime | None
    lease_token: UUID
    traceparent: str | None


type JobHandler = Callable[[Job], Awaitable[JobOutcome]]
"""Runs outside any unit: it reads in one, calls out, records in another (no transaction spans a call out)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class JobSpec:
    """What an assembly hands: a declared job kind and its handler."""

    kind: str
    handler: JobHandler
