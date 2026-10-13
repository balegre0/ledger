"""The fan-out on real PostgreSQL (§3.5): outbox rows into their consumers' inboxes, as ``ledger_relay``.

Absence is proved by draining (``run_until_idle`` returns when a pass finds nothing), never by waiting.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import Callable, Mapping

import pytest
from psycopg import sql
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
lazy import psycopg
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.database import Database
from ledger_platform.events import Dispatcher, PostgresEventBus
from ledger_testing.database import app_url
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.postgres import WorkerDatabase

type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]

TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
SUBJECT: Final = uuid.uuid7()
SCHEMAS: Final = ("platform", "probe", "other")
RENAMES: Final = {"other.renames": frozenset({"probe.thing.renamed"})}
ECHO: Final = {"probe.echo": frozenset({"probe.thing.renamed"})}
PUBLISHED: Final = """
    SELECT id, type, version, source, organization_id, company_id, mode, subject, aggregate_version,
           occurred_at, traceparent, payload
    FROM probe.outbox ORDER BY id
"""
DELIVERED: Final = """
    SELECT event_id, type, version, source, organization_id, company_id, mode, subject, aggregate_version,
           occurred_at, traceparent, payload,
           consumer, status, attempts, next_attempt_at, lease_until, lease_token, last_error_code
    FROM {} ORDER BY event_id
"""
_ROWS: Final = TypeAdapter(list[tuple[object, ...]])
_COUNT: Final = TypeAdapter(int)
_MOMENT: Final = TypeAdapter(datetime)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


@final
@dataclass(slots=True)
class RecordedSignals:
    """``Signals`` that remember what they were told, in order."""

    sent: list[Channel] = field(default_factory=list)

    def signal(self, channel: Channel) -> None:
        self.sent.append(channel)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Bench:
    """The producer, a dispatcher over every schema, the subscriptions, and a superuser to look."""

    probe: BoundDatabase
    dispatcher: Dispatcher
    signals: RecordedSignals
    subscribe: Subscribe
    superuser: psycopg.AsyncConnection

    async def publish(self, count: int = 1) -> None:
        async with self.probe.unit(TENANT) as uow:
            for index in range(count):
                uow.publish(renamed(f"name-{index}"))

    async def rows(self, query: LiteralString | sql.Composed) -> list[tuple[object, ...]]:
        cursor = await self.superuser.execute(query)
        return _ROWS.validate_python([tuple(row) for row in await cursor.fetchall()])

    async def count(self, schema: str, table: str) -> int:
        ((found,),) = await self.rows(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
        )
        return _COUNT.validate_python(found)

    async def now(self) -> datetime:
        ((moment,),) = await self.rows("SELECT clock_timestamp()")
        return _MOMENT.validate_python(moment)

    def dispatcher_like(self, registry: CollectorRegistry | None = None) -> Dispatcher:
        """Another dispatcher on the same pool, as a second process or a restart would run."""
        return Dispatcher(self.probe.database, schemas=SCHEMAS, signals=self.signals, registry=registry)


def renamed(name: str = "x") -> Renamed:
    return Renamed(scope=TENANT, subject=SUBJECT, aggregate_version=1, name=name)


def unrouted(registry: CollectorRegistry, schema: str) -> float | None:
    return registry.get_sample_value("ledger_outbox_unrouted_total", {"schema": schema})


@pytest.fixture
def bench(
    probe: BoundDatabase,
    subscribe: Subscribe,
    superuser: psycopg.AsyncConnection,
    registry: CollectorRegistry,
) -> Bench:
    signals = RecordedSignals()
    dispatcher = Dispatcher(probe.database, schemas=SCHEMAS, signals=signals, registry=registry)
    return Bench(
        probe=probe, dispatcher=dispatcher, signals=signals, subscribe=subscribe, superuser=superuser
    )


async def test_an_event_reaches_every_subscribed_consumer_once_and_leaves_the_outbox(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    bench.subscribe("probe", ECHO)
    await bench.publish(2)
    published = await bench.rows(PUBLISHED)
    before = await bench.now()
    assert await bench.dispatcher.run_until_idle() == 2
    after = await bench.now()
    for schema, consumer in (("other", "other.renames"), ("probe", "probe.echo")):
        delivered = await bench.rows(sql.SQL(DELIVERED).format(sql.Identifier(schema, "inbox")))
        assert [row[:12] for row in delivered] == published
        assert [row[12:15] for row in delivered] == [(consumer, "ready", 0)] * 2
        for row in delivered:
            assert before <= _MOMENT.validate_python(row[15]) <= after
            assert row[16:] == (None, None, None)
    assert await bench.count("probe", "outbox") == 0
    assert bench.signals.sent == ["ledger_inbox"]


async def test_an_event_published_through_the_bus_is_fanned_out(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    async with bench.probe.unit(TENANT) as uow:
        PostgresEventBus().publish(uow, renamed())
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.rows("SELECT consumer, payload FROM other.inbox") == [("other.renames", {"name": "x"})]


async def test_an_event_nobody_subscribes_to_is_kept_and_counted_until_its_subscription_arrives(
    bench: Bench, registry: CollectorRegistry
) -> None:
    await bench.publish()
    before = await bench.now()
    assert await bench.dispatcher.run_until_idle() == 1
    after = await bench.now()
    ((marked,),) = await bench.rows("SELECT unrouted_since FROM probe.outbox")
    assert before <= _MOMENT.validate_python(marked) <= after
    assert unrouted(registry, "probe") == 1
    assert unrouted(registry, "other") == 0
    assert await bench.dispatcher.run_until_idle() == 0  # kept, and not taken again: no busy loop
    assert bench.signals.sent == []
    bench.subscribe("other", {"other.audits": frozenset({"probe.thing.archived"})})  # another type
    assert await bench.dispatcher.run_until_idle() == 0
    assert await bench.count("probe", "outbox") == 1
    bench.subscribe("other", RENAMES)  # the rollout of its consumer (Review Focus 3)
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.rows("SELECT consumer FROM other.inbox") == [("other.renames",)]
    assert await bench.count("probe", "outbox") == 0
    assert unrouted(registry, "probe") == 1


async def test_a_new_dispatcher_fans_out_what_an_older_one_left_unrouted(
    bench: Bench, registry: CollectorRegistry
) -> None:
    await bench.publish()
    assert await bench.dispatcher.run_until_idle() == 1
    bench.subscribe("other", RENAMES)
    restarted = bench.dispatcher_like(registry)  # a rolling deploy: the same counter, exported again
    assert await restarted.run_until_idle() == 1
    assert await bench.count("other", "inbox") == 1
    assert unrouted(registry, "probe") == 1


async def test_a_removed_subscription_stops_new_fan_out_and_keeps_what_was_delivered(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    await bench.publish()
    assert await bench.dispatcher.run_until_idle() == 1
    bench.subscribe("other", {})
    await bench.publish()
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.count("other", "inbox") == 1
    assert await bench.rows("SELECT count(*) FROM probe.outbox WHERE unrouted_since IS NOT NULL") == [(1,)]


async def test_two_dispatchers_racing_deliver_each_event_once(bench: Bench, chassis: WorkerDatabase) -> None:
    bench.subscribe("other", RENAMES)
    await bench.publish(50)
    first = Database.create(app_url(chassis), pool_size=2)
    second = Database.create(app_url(chassis), pool_size=2)
    try:
        racing = [
            Dispatcher(each, schemas=SCHEMAS, signals=bench.signals, batch=7) for each in (first, second)
        ]
        handled = await asyncio.gather(*(each.run_until_idle() for each in racing))
    finally:
        await first.aclose()
        await second.aclose()
    assert sum(handled) == 50
    assert await bench.count("other", "inbox") == 50
    assert await bench.count("probe", "outbox") == 0


async def test_a_redelivered_outbox_row_never_duplicates_an_inbox_row(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    await bench.publish()
    await bench.superuser.execute("CREATE TEMPORARY TABLE kept AS SELECT * FROM probe.outbox")
    assert await bench.dispatcher.run_until_idle() == 1
    await bench.superuser.execute("INSERT INTO probe.outbox SELECT * FROM kept")
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.count("other", "inbox") == 1
    assert await bench.count("probe", "outbox") == 0


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM probe.inbox",
        "SELECT * FROM probe.audit_events",
        (
            "INSERT INTO probe.jobs (kind, key, status, run_at, max_attempts, created_at, updated_at)"
            " VALUES ('k', 'k', 'ready', now(), 1, now(), now())"
        ),
        "UPDATE probe.outbox SET payload = '{}'",
    ],
)
async def test_the_relay_role_reaches_the_queue_tables_only(pool: Database, statement: str) -> None:
    async with pool.engine.connect() as connection, connection.begin():
        await connection.execute(text("SET LOCAL ROLE ledger_relay"))
        await connection.execute(text("SELECT * FROM probe.outbox"))
        await connection.execute(text("SELECT * FROM probe.subscriptions"))
        await connection.execute(text("SELECT * FROM platform.routing"))
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(text(statement))
