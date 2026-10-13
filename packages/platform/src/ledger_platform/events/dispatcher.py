"""The fan-out (§3.5): outbox rows of every schema into the inboxes of their consumers, as ``ledger_relay``.

One transaction per schema and pass: claim up to ``batch`` outbox rows (``FOR UPDATE SKIP LOCKED``, oldest
first), insert one inbox row per subscribed consumer (``ON CONFLICT DO NOTHING``: a repeat is absorbed),
delete the outbox rows; rows nobody subscribes to are kept and marked ``unrouted_since`` (counted, alerted),
never deleted. The routing table is cached and reloaded when ``platform.routing.version`` moves (one read per
pass, in a short transaction of its own); every load makes the unrouted rows of the types it routes due again,
so a row marked by a dispatcher whose table was a version behind is fanned out at that dispatcher's next pass,
or by whichever dispatcher starts next. A pass that moved rows signals ``ledger_inbox`` after its commits.

Times are the database's: ``unrouted_since`` is the relay transaction's ``now()``, and an inbox row is due
from its insert (the column's default).
"""

import weakref
from dataclasses import dataclass
from typing import Final, Never, final
lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from uuid import UUID

from prometheus_client import Counter
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
lazy from prometheus_client import CollectorRegistry
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_platform.data.notifier import INBOX, OUTBOX
from ledger_platform.data.tables import chassis_tables
from ledger_platform.events.routing import load_routing, routing_version
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.events.routing import Routing
lazy from ledger_platform.events.wakeups import Waker

_RELAY: Final = text("SET LOCAL ROLE ledger_relay")
_COPIED: Final = (
    "type",
    "version",
    "source",
    "organization_id",
    "company_id",
    "mode",
    "subject",
    "aggregate_version",
    "occurred_at",
    "traceparent",
    "payload",
)
POLL: Final = 1.0
"""Seconds between passes when no NOTIFY arrives (§3.5's safety net)."""
_COUNTERS: Final[weakref.WeakKeyDictionary[CollectorRegistry, Counter]] = weakref.WeakKeyDictionary()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _OutboxRow:
    """An outbox row's id and envelope, as the fan-out copies them."""

    id: UUID
    type: str
    version: int
    source: str
    organization_id: UUID | None
    company_id: UUID | None
    mode: str | None
    subject: UUID | None
    aggregate_version: int | None
    occurred_at: datetime
    traceparent: str | None
    payload: dict[str, JsonValue]

    def delivery(self, consumer: str) -> dict[str, object]:
        """The inbox row of ``consumer`` for this event."""
        return {
            "consumer": consumer,
            "event_id": self.id,
            "type": self.type,
            "version": self.version,
            "source": self.source,
            "organization_id": self.organization_id,
            "company_id": self.company_id,
            "mode": self.mode,
            "subject": self.subject,
            "aggregate_version": self.aggregate_version,
            "occurred_at": self.occurred_at,
            "traceparent": self.traceparent,
            "payload": self.payload,
        }


_ROWS: Final = TypeAdapter(list[_OutboxRow])


def _unrouted_counter(registry: CollectorRegistry | None) -> Counter:
    """``ledger_outbox_unrouted_total{schema}``: one per registry, shared by its dispatchers."""
    found = None if registry is None else _COUNTERS.get(registry)
    if found is not None:
        return found
    counter = Counter(
        "ledger_outbox_unrouted",
        "Events kept because no consumer subscribed to their type.",
        ["schema"],
        registry=registry,
    )
    if registry is not None:
        _COUNTERS[registry] = counter
    return counter


@final
class Dispatcher:
    """See the module."""

    __slots__ = ("_batch", "_database", "_routing", "_schemas", "_signals", "_unrouted")

    def __init__(
        self,
        database: Database,
        *,
        schemas: Sequence[str],
        signals: Signals,
        batch: int = 100,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self._database = database
        self._schemas = tuple(schemas)
        self._signals = signals
        self._batch = batch
        self._routing: Routing | None = None
        self._unrouted = _unrouted_counter(registry)
        for schema in self._schemas:
            self._unrouted.labels(schema=schema)  # exported at zero: a first event must show in increase()

    async def _current(self) -> Routing:
        """The routing table, reloaded when its version moved; a load makes newly routed rows due again."""
        async with self._database.engine.begin() as connection:
            await connection.execute(_RELAY)
            cached = self._routing
            if cached is not None and cached.version == await routing_version(connection):
                return cached
            fresh = await load_routing(connection, self._schemas)
            if fresh.routes:
                await self._reroute(connection, tuple(fresh.routes))
        self._routing = fresh  # only once committed: a failed reroute is tried again at the next pass
        return fresh

    async def _reroute(self, connection: AsyncConnection, types: tuple[str, ...]) -> None:
        for schema in self._schemas:
            outbox = chassis_tables(schema).outbox
            await connection.execute(
                update(outbox)
                .where(outbox.c.unrouted_since.is_not(None), outbox.c.type.in_(types))
                .values(unrouted_since=None)
            )

    async def _pass(self, schema: str, routing: Routing) -> tuple[int, bool]:
        outbox = chassis_tables(schema).outbox
        due = (
            select(outbox.c.id, *(outbox.c[column] for column in _COPIED))
            .where(outbox.c.unrouted_since.is_(None))
            .order_by(outbox.c.id)
            .limit(self._batch)
            .with_for_update(skip_locked=True)
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(_RELAY)
            rows = _ROWS.validate_python([dict(row) for row in (await connection.execute(due)).mappings()])
            deliveries: dict[str, list[dict[str, object]]] = {}
            routed: list[UUID] = []
            unrouted: list[UUID] = []
            for row in rows:
                routes = routing.routes.get(row.type, ())
                (routed if routes else unrouted).append(row.id)
                for route in routes:
                    deliveries.setdefault(route.schema, []).append(row.delivery(route.consumer))
            for target, values in deliveries.items():
                await connection.execute(
                    insert(chassis_tables(target).inbox).values(values).on_conflict_do_nothing()
                )
            if routed:
                await connection.execute(delete(outbox).where(outbox.c.id.in_(routed)))
            if unrouted:
                await connection.execute(
                    update(outbox).where(outbox.c.id.in_(unrouted)).values(unrouted_since=func.now())
                )
        if unrouted:
            self._unrouted.labels(schema=schema).inc(len(unrouted))
        return len(rows), bool(routed)

    async def run_once(self) -> int:
        """One pass over every schema; how many outbox rows it fanned out or marked unrouted."""
        routing = await self._current()
        handled, delivered = 0, False
        for schema in self._schemas:
            count, moved = await self._pass(schema, routing)
            handled += count
            delivered |= moved
        if delivered:
            self._signals.signal(INBOX)
        return handled

    async def run_until_idle(self) -> int:
        """Pass until a pass finds nothing (tests drain with it: absence is proved by draining, §14.2)."""
        total = 0
        while handled := await self.run_once():
            total += handled
        return total

    async def run(self, wakeups: Waker) -> Never:
        """Forever: drain, then wait for a NOTIFY on ``ledger_outbox`` or the poll (the supervised loop)."""
        woken = wakeups.subscribe(OUTBOX)
        while True:
            await self.run_until_idle()
            await wakeups.wait(woken, poll=POLL)
