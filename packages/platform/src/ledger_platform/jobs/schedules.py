"""Schedules on lease rows (§3.6, Decision 15): every replica runs a ``Scheduler``, each slot fires once.

A schedule's state is its row in ``platform.schedule_leases`` (``name, holder, leased_until, next_run_at``),
made on first sight with ``next_run_at = now()``. A due slot is taken in a short ``platform`` unit (``FOR
UPDATE SKIP LOCKED``, a lease from the statement's time, the scheduler as holder): of two schedulers racing,
one takes it and the other skips it. The holder then enqueues the schedule's job in its target schema, keyed
``<name>@<slot>``, and moves ``next_run_at`` to the first slot after the database's ``now()`` and releases
the lease, only if it still holds it for that slot. Missed slots are not replayed: a scheduler that was down
for three periods fires once. Each scheduler holds leases under a name of its own (host, process and a
random part), so two replicas never take each other for the holder.

A scheduler that dies between the enqueue and the release leaves a lease that runs out; the next scheduler
takes the same slot and enqueues the same key again, which the queue ignores whatever that job's state, even
done or dead (``restart=False``): a slot runs its job once. A schedule that fails (its target refuses the
job) is logged and the others still fire; its lease runs out and the slot is tried again. A schedule only
enqueues: the work is the job's.
"""

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Final, Never, final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import structlog
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as upsert

from ledger_kernel.errors import InvalidValueError
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.tables import platform_tables
lazy from ledger_platform.data.bound import BoundDatabase

POLL: Final = 1.0
"""Seconds between two looks at the schedules: the finest cadence a schedule can have."""
LEASE: Final = timedelta(seconds=30)
"""How long a scheduler holds a slot it took, unless told otherwise."""
_MOMENT: Final = TypeAdapter(AwareDatetime)
_logger = structlog.stdlib.get_logger(__name__)


@final
@dataclass(frozen=True, slots=True)
class Schedule:
    """Every ``every``, enqueue a job of kind ``enqueues`` into ``target`` (``[[schedules]]``)."""

    name: str
    every: timedelta
    enqueues: str
    target: BoundDatabase

    def __post_init__(self) -> None:
        """A positive interval, and a job kind the target declares (``UndeclaredJobKindError``)."""
        if self.every <= timedelta(0):
            raise InvalidValueError(field="schedule.every", requirement="a positive interval")
        self.target.policy_of(self.enqueues)


def _key(name: str, slot: datetime) -> str:
    return f"{name}@{slot.astimezone(UTC).isoformat()}"


def _holder() -> str:
    """A name no other scheduler has: this host, this process, and a random part for this instance."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid7()}"


@final
class Scheduler:
    """Fires the schedules' slots once across replicas (see the module)."""

    __slots__ = ("_holder", "_known", "_lease", "_platform", "_schedules")

    def __init__(
        self,
        *,
        platform: BoundDatabase,
        schedules: Sequence[Schedule],
        holder: str | None = None,
        lease: timedelta = LEASE,
    ) -> None:
        self._platform = platform
        self._schedules = tuple(schedules)
        self._holder = _holder() if holder is None else holder
        self._lease = lease
        self._known = False

    @property
    def holder(self) -> str:
        """The name this scheduler holds leases under."""
        return self._holder

    async def run_due(self) -> int:
        """Fire every schedule whose slot has come and whose lease is free; how many fired.

        The first call makes the lease rows of schedules seen for the first time; a schedule that fails is
        logged (``schedule_failed``) and the others still fire.
        """
        if not self._known:
            await self._make_lease_rows()
            self._known = True
        fired = 0
        for schedule in self._schedules:
            try:
                fired += await self._fire(schedule)
            except Exception:  # one schedule's target failing must not keep the others from firing
                _logger.exception("schedule_failed", schedule=schedule.name)
        return fired

    async def _make_lease_rows(self) -> None:
        leases = platform_tables().schedule_leases
        async with self._platform.unit(None) as unit:
            for schedule in self._schedules:
                await unit.connection.execute(
                    upsert(leases)
                    .values(name=schedule.name, next_run_at=func.now())
                    .on_conflict_do_nothing(index_elements=(leases.c.name,))
                )

    async def _fire(self, schedule: Schedule) -> int:
        """Fire ``schedule``'s due slot if this scheduler takes it: 1 when it did, else 0."""
        slot = await self._take(schedule)
        if slot is None:
            return 0
        key = _key(schedule.name, slot)
        with acting_as(Actor(f"system:schedule:{schedule.name}", key)):
            async with schedule.target.unit(None) as unit:
                unit.jobs.enqueue(schedule.enqueues, key, restart=False)
        await self._release(schedule, slot)
        return 1

    async def run(self) -> Never:
        """``run_due`` forever, every ``POLL`` seconds."""
        while True:
            await self.run_due()
            await asyncio.sleep(POLL)

    async def _take(self, schedule: Schedule) -> datetime | None:
        """The due slot of ``schedule``, leased to this scheduler; none when not due or leased by another."""
        leases = platform_tables().schedule_leases
        free = (
            select(leases.c.name)
            .where(
                leases.c.name == schedule.name,
                leases.c.next_run_at <= func.now(),
                or_(leases.c.leased_until.is_(None), leases.c.leased_until < func.now()),
            )
            .with_for_update(skip_locked=True)
            .cte("free")
        )
        async with self._platform.unit(None) as unit:
            taken = (
                await unit.connection.execute(
                    update(leases)
                    .where(leases.c.name == free.c.name)
                    .values(holder=self._holder, leased_until=func.statement_timestamp() + self._lease)
                    .returning(leases.c.next_run_at)
                )
            ).first()
        return None if taken is None else _MOMENT.validate_python(taken[0])

    async def _release(self, schedule: Schedule, slot: datetime) -> None:
        """Move to the first slot after now and free the lease, if this scheduler holds it for ``slot``."""
        leases = platform_tables().schedule_leases
        every = schedule.every
        async with self._platform.unit(None) as unit:
            after = slot + every * (1 + (unit.now - slot) // every)
            await unit.connection.execute(
                update(leases)
                .where(
                    leases.c.name == schedule.name,
                    leases.c.holder == self._holder,
                    leases.c.next_run_at == slot,
                )
                .values(next_run_at=after, holder=None, leased_until=None)
            )
