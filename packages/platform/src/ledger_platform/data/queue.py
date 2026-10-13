"""The claim/lease/fencing/retry engine shared by inboxes and jobs (§3.5, §3.6).

A claim selects due rows ``FOR UPDATE SKIP LOCKED`` (two workers never claim one row), marks them
``running`` with a lease (``lease_until``) and a fresh fencing token (``lease_token``), and counts the attempt
on the claim, so a worker that crashes mid-run still used it. A row whose lease ran out is due again (the
caller's ``due`` says so). Whoever records the outcome names the token; a late writer whose lease was taken
over records nothing.
"""

from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final, NotRequired, TypedDict, Unpack, final
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime

from sqlalchemy import func, select, update
lazy from sqlalchemy import ColumnElement, RowMapping
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


class Window(TypedDict):
    """Which due rows a claim takes, and for how long."""

    order_by: Sequence[ColumnElement[object]]
    """The order in which due rows are taken (oldest first, usually)."""
    limit: int
    """At most this many rows."""
    now: datetime
    """The claim's time: the lease runs from it."""
    lease: timedelta
    """How long the claimer holds a row before it is due again."""
    extra: NotRequired[Mapping[str, object]]
    """More columns to set on the claimed rows (``updated_at``)."""


async def claim(
    connection: AsyncConnection,
    table: AnyTable,
    *,
    keys: Sequence[str],
    due: ColumnElement[bool],
    **window: Unpack[Window],
) -> Sequence[RowMapping]:
    """Claim up to ``limit`` rows of ``table`` that are ``due`` (see the module): the claimed rows, as stored.

    ``keys`` are the primary key's columns. ``RETURNING`` keeps no order: a caller that needs one sorts.
    """
    chosen = (
        select(*(table.c[key] for key in keys))
        .where(due)
        .order_by(*window["order_by"])
        .limit(window["limit"])
        .with_for_update(skip_locked=True)
        .cte("chosen")
    )
    claimed = (
        update(table)
        .where(*(table.c[key] == chosen.c[key] for key in keys))
        .values(
            status="running",
            lease_until=window["now"] + window["lease"],
            lease_token=func.gen_random_uuid(),
            attempts=table.c.attempts + 1,
            **window.get("extra", _NOTHING),
        )
        .returning(table)
    )
    rows: Sequence[RowMapping] = (await connection.execute(claimed)).mappings().all()
    return rows
