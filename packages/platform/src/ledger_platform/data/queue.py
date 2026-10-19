"""The claim/lease/fencing/retry engine shared by inboxes and jobs (§3.5, §3.6).

A claim selects due rows ``FOR UPDATE SKIP LOCKED`` (two workers never claim one row), marks them ``running``
with a lease (``lease_until``) and a fresh fencing token (``lease_token``), and counts the attempt on the
claim, so a worker that crashes mid-run still used it. Whoever records the outcome names the token; a late
writer whose lease was taken over records nothing.

A row whose lease ran out (its worker died) is recovered before the claim, the way a failed run is recorded:
``ready`` again a backoff after its lease ran out, or ``dead`` with ``worker_lost`` once its attempts are
spent, so a crash loop ends. The claim then takes ``ready`` rows only. Each statement reads one partial index
(``*_due`` for the claim, ``*_expired`` for the recovery) in its order, under its ``LIMIT``: neither sorts the
due set.

Time is the database's, so every worker measures against one clock, whatever its host's says (§3.6). A lease
runs from the claiming statement's time (``statement_timestamp()``), not from its transaction's start
(``now()``, frozen when the transaction began): a claim made late in a transaction still holds its rows for
the whole lease. Callers claim in short units of their own, where the two differ by milliseconds, and their
``due`` predicates compare with ``now()``.

A worker that stops gives back what it claimed and holds (``release``): the rows are ``ready`` again at once,
their leases cleared, instead of waiting for the leases to run out. A row whose handler never started gets
its attempt back; one whose handler started (and was stopped) keeps it, as a crash would. Its loop then ends
in ``parked`` (a worker never returns: the runtime's ``Supervisor`` cancels it), and a loop that carries on
past every ``Exception`` asks ``being_cancelled`` whether the one it caught is really its own cancellation.

A handler that runs outside the unit that claimed its row (a job's, an outside consumer's) keeps its lease by
renewing it while it runs (``renewing``): a renewal that finds the row taken over, or that fails, stops the
handler, since an unrenewed lease runs out and another worker would run the row too.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final, Never, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence

from pydantic import TypeAdapter
from sqlalchemy import case, func, select, tuple_, update
lazy from sqlalchemy import ColumnElement, RowMapping, Update
lazy from sqlalchemy.ext.asyncio import AsyncConnection

lazy from ledger_platform.data.database import AnyTable


@final
@dataclass(frozen=True, slots=True)
class Backoff:
    """Waits after the 1st, 2nd, ... failed attempt, then ``then`` for every later one."""

    steps: tuple[timedelta, ...]
    then: timedelta

    def after(self, attempts: int) -> timedelta:
        """How long after the failure of attempt number ``attempts`` (counted from 1) the row is due again."""
        return self.steps[attempts - 1] if 1 <= attempts <= len(self.steps) else self.then


DEFAULT_BACKOFF: Final = Backoff(
    steps=(timedelta(seconds=1), timedelta(seconds=10), timedelta(minutes=1), timedelta(minutes=5)),
    then=timedelta(minutes=10),
)
"""§3.5: 1 s, 10 s, 1 min, 5 min, then every 10 min."""
_NOTHING: Final[Mapping[str, object]] = MappingProxyType({})
WORKER_LOST: Final = "worker_lost"
"""The code of a row whose lease ran out: retried after the backoff, or dead once its attempts are spent."""
RECOVERY_BATCH: Final = 100
"""Expired rows a recovery takes at most (the rest at the next claim)."""
_STATUSES: Final = TypeAdapter(list[str])


class Window(TypedDict):
    """Which due rows a claim takes, and for how long."""

    order_by: Sequence[ColumnElement[object]]
    """The order in which due rows are taken (oldest first, usually)."""
    limit: int
    """At most this many rows."""
    lease: timedelta
    """How long the claimer holds a row before it is due again."""
    extra: NotRequired[Mapping[str, object]]
    """More columns to set on the claimed rows (``updated_at``)."""


def claim_statement(
    table: AnyTable,
    *,
    keys: Sequence[str],
    due: ColumnElement[bool],
    **window: Unpack[Window],
) -> Update:
    """The ``UPDATE`` that ``claim`` runs (for ``EXPLAIN``, and for ``claim``)."""
    chosen = (
        select(*(table.c[key] for key in keys))
        .where(due)
        .order_by(*window["order_by"])
        .limit(window["limit"])
        .with_for_update(skip_locked=True)
        .cte("chosen")
    )
    return (
        update(table)
        .where(*(table.c[key] == chosen.c[key] for key in keys))
        .values(
            status="running",
            lease_until=func.statement_timestamp() + window["lease"],
            lease_token=func.gen_random_uuid(),
            attempts=table.c.attempts + 1,
            **window.get("extra", _NOTHING),
        )
        .returning(table)
    )


_WOKEN: Final = "woken_at"
"""The jobs' column a wake marks a running job in (``chassis_schema_v2``): no other queue has it."""
RELEASE_TIMEOUT: Final = 2.0
"""Seconds a stopping worker gives the statement that releases its rows."""


def release_statement(
    table: AnyTable, *, keys: Sequence[str], claimed: Sequence[tuple[object, ...]], refund: bool
) -> Update:
    """Make ``claimed`` (each row's keys, then its lease token) ``ready`` again while this worker holds them.

    ``refund`` gives back the attempt the claim counted (the handler never started). A job's wake mark
    (``woken_at``) goes with the run it was left on.
    """
    held = tuple_(*(table.c[key] for key in keys), table.c.lease_token)
    unmarked = {_WOKEN: None} if _WOKEN in table.c else {}
    return (
        update(table)
        .where(held.in_(claimed), table.c.status == "running")
        .values(
            status="ready",
            lease_until=None,
            lease_token=None,
            attempts=table.c.attempts - int(refund),
            **unmarked,
        )
    )


async def release(
    connection: AsyncConnection,
    table: AnyTable,
    *,
    keys: Sequence[str],
    started: Sequence[tuple[object, ...]],
    unstarted: Sequence[tuple[object, ...]],
) -> None:
    """Give back the rows a stopping worker holds (see the module): the started ones keep their attempt."""
    for claimed, refund in ((started, False), (unstarted, True)):
        if claimed:
            await connection.execute(release_statement(table, keys=keys, claimed=claimed, refund=refund))


async def claim(
    connection: AsyncConnection,
    table: AnyTable,
    *,
    keys: Sequence[str],
    due: ColumnElement[bool],
    **window: Unpack[Window],
) -> Sequence[RowMapping]:
    """Claim up to ``limit`` rows of ``table`` that are ``due`` (see the module): the claimed rows, as stored.

    ``keys`` are the primary key's columns; ``due`` names ``ready`` rows (``recover`` first makes expired ones
    ready). ``RETURNING`` keeps no order: a caller that needs one sorts.
    """
    rows: Sequence[RowMapping] = (
        (await connection.execute(claim_statement(table, keys=keys, due=due, **window))).mappings().all()
    )
    return rows


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Recovered:
    """What a recovery did: rows made ``dead`` (attempts spent) and rows made ``ready`` again."""

    dead: int
    ready: int


class Recovery(TypedDict):
    """How expired rows of a group are recovered."""

    due_column: str
    """The column that says when a ``ready`` row is due (``run_at``, ``next_attempt_at``)."""
    max_attempts: int | ColumnElement[object]
    """Attempts after which the row is dead: a number, or the row's own column."""
    backoff: Backoff
    """The wait after the attempt the dead worker used, counted from when its lease ran out."""
    limit: NotRequired[int]
    """At most this many rows (``RECOVERY_BATCH``)."""
    extra: NotRequired[Mapping[str, object]]
    """More columns to set on the recovered rows (``updated_at``)."""


def recovery_statement(
    table: AnyTable, *, keys: Sequence[str], group: ColumnElement[bool], **recovery: Unpack[Recovery]
) -> Update:
    """The ``UPDATE`` that ``recover`` runs (for ``EXPLAIN``, and for ``recover``)."""
    expired = (
        select(*(table.c[key] for key in keys))
        .where(group, table.c.status == "running", table.c.lease_until < func.now())
        .order_by(table.c.lease_until)
        .limit(recovery.get("limit", RECOVERY_BATCH))
        .with_for_update(skip_locked=True)
        .cte("expired")
    )
    backoff = recovery["backoff"]
    wait = case(
        *((table.c.attempts == number, step) for number, step in enumerate(backoff.steps, start=1)),
        else_=backoff.then,
    )
    due_column = recovery["due_column"]
    spent = table.c.attempts >= recovery["max_attempts"]
    return (
        update(table)
        .where(*(table.c[key] == expired.c[key] for key in keys))
        .values(
            {
                "status": case((spent, "dead"), else_="ready"),
                due_column: case((spent, table.c[due_column]), else_=table.c.lease_until + wait),
                "lease_until": None,
                "lease_token": None,
                "last_error_code": WORKER_LOST,
                **recovery.get("extra", _NOTHING),
            }
        )
        .returning(table.c.status)
    )


async def recover(
    connection: AsyncConnection,
    table: AnyTable,
    *,
    keys: Sequence[str],
    group: ColumnElement[bool],
    **recovery: Unpack[Recovery],
) -> Recovered:
    """Recover ``group``'s rows whose lease ran out (see the module): how many became dead, how many ready."""
    statuses = _STATUSES.validate_python(
        (await connection.execute(recovery_statement(table, keys=keys, group=group, **recovery)))
        .scalars()
        .all()
    )
    return Recovered(dead=statuses.count("dead"), ready=statuses.count("ready"))


async def renewing[R](
    work: Awaitable[R], *, renew: Callable[[], Awaitable[bool]], every: float, lost: Callable[[], Exception]
) -> R:
    """``work``'s result, ``renew`` awaited every ``every`` seconds while it runs (see the module).

    A renewal that answers ``False`` cancels ``work`` and raises ``lost()`` instead of the cancellation: one
    that fails must answer ``False`` itself. The cancellation of the task awaiting this is raised as it is.
    """
    running = asyncio.ensure_future(work)
    gone = asyncio.Event()
    keeper = asyncio.create_task(_keep(running, renew, every, gone))
    try:
        return await running
    except asyncio.CancelledError:
        if gone.is_set() and not being_cancelled():
            raise lost() from None
        raise
    finally:
        keeper.cancel()
        await asyncio.gather(keeper, return_exceptions=True)


async def _keep[R](
    running: asyncio.Future[R], renew: Callable[[], Awaitable[bool]], every: float, gone: asyncio.Event
) -> None:
    while True:
        await asyncio.sleep(every)
        if not await renew():
            gone.set()
            running.cancel()
            return


async def parked() -> Never:
    """Wait to be cancelled: where a stopped worker's loop ends (a worker never returns)."""
    forever: asyncio.Future[None] = asyncio.get_running_loop().create_future()  # nothing completes it
    while True:
        await forever


def being_cancelled() -> bool:
    """Whether the task running this has been asked to stop.

    For a loop that catches every ``Exception`` to carry on: a clean-up that failed while the task was being
    cancelled (a rollback on a broken connection) turns the cancellation into its own error, and such a loop
    must end on it all the same, by raising it again.
    """
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0
