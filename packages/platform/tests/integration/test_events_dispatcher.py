"""The fan-out on real PostgreSQL (§3.5): outbox rows into their consumers' inboxes, as ``ledger_relay``.

Absence is proved by draining (``run_until_idle`` returns when a pass finds nothing), never by waiting.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Callable, Mapping

import pytest
from psycopg import sql
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from structlog.testing import capture_logs
lazy import psycopg
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.database import Database
from ledger_platform.events import Dispatcher, PostgresEventBus
from ledger_platform.events.dispatcher import ROWS_PER_INSERT, chunked
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
POISON: Final = """
    CREATE FUNCTION other.refuse_poison() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.payload ->> 'name' = 'poison' THEN RAISE EXCEPTION 'refused by the test'; END IF;
        RETURN NEW;
    END $$
"""
POISON_TRIGGER: Final = """
    CREATE TRIGGER refuse_poison BEFORE INSERT ON other.inbox
    FOR EACH ROW EXECUTE FUNCTION other.refuse_poison()
"""
COPY_TO_OTHER: Final = """
    INSERT INTO other.outbox
    SELECT gen_random_uuid(), type, version, source, organization_id, company_id, mode, subject,
           aggregate_version, occurred_at, traceparent, payload
    FROM probe.outbox
"""
PERMISSION_TRIGGER: Final = """
    CREATE OR REPLACE FUNCTION other.refuse_poison() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.payload ->> 'name' = 'poison' THEN
            RAISE insufficient_privilege USING MESSAGE = 'refused like a permission';
        END IF;
        RETURN NEW;
    END $$
"""
"""A row-specific failure dressed as a systemic one: only the row beside it tells them apart."""
CREATED_IN_OTHER: Final = """
    INSERT INTO other.outbox (id, type, version, source, occurred_at, payload)
    VALUES (gen_random_uuid(), 'probe.thing.created', 1, 'probe', now(), '{}')
"""
HOLD_PROBE: Final = "SELECT pg_advisory_xact_lock(hashtext('ledger.fan_out'), hashtext('probe'))"
_ROWS: Final = TypeAdapter(list[tuple[object, ...]])
_COUNT: Final = TypeAdapter(int)
_TEXT: Final = TypeAdapter(str)
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
        await self.publish_named(*(f"name-{index}" for index in range(count)))

    async def publish_named(self, *names: str) -> None:
        async with self.probe.unit(TENANT) as uow:
            for name in names:
                uow.publish(renamed(name))

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


def failed(registry: CollectorRegistry, schema: str) -> float | None:
    return registry.get_sample_value("ledger_outbox_failed_total", {"schema": schema})


@pytest.fixture
async def poisoned(superuser: psycopg.AsyncConnection) -> AsyncGenerator[None]:
    """``other.inbox`` refuses any event named ``poison``: a row the fan-out can never deliver."""
    await superuser.execute(POISON)
    await superuser.execute(POISON_TRIGGER)
    try:
        yield
    finally:
        await superuser.execute("DROP FUNCTION IF EXISTS other.refuse_poison() CASCADE")


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


async def test_a_row_that_cannot_be_fanned_out_is_set_aside_and_the_others_go(
    bench: Bench, registry: CollectorRegistry, poisoned: None
) -> None:
    del poisoned
    bench.subscribe("other", RENAMES)
    await bench.publish_named("a", "poison", "b")
    ((poison,),) = await bench.rows("SELECT id FROM probe.outbox WHERE payload ->> 'name' = 'poison'")
    with capture_logs() as logs:
        assert await bench.dispatcher.run_until_idle() == 2  # the good rows; a failure is no progress
        for _ in range(9):  # later passes (the poll's): the poison row fails alone, attempt after attempt
            assert await bench.dispatcher.run_once() == 0
    assert await bench.rows("SELECT payload ->> 'name' FROM other.inbox ORDER BY 1") == [("a",), ("b",)]
    assert await bench.rows("SELECT id, attempts, unrouted_since FROM probe.outbox") == [(poison, 10, None)]
    assert failed(registry, "probe") == 10
    assert failed(registry, "other") == 0
    assert len(logs) == 10
    for logged in logs:
        assert _TEXT.validate_python(logged.pop("error")).startswith("sqlalchemy.exc.")
        assert logged == {
            "event": "outbox.fan_out_failed",
            "schema": "probe",
            "event_id": str(poison),
            "event_type": "probe.thing.renamed",
            "attempts": logged["attempts"],
            "log_level": "warning",
        }
    assert [logged["attempts"] for logged in logs] == list(range(1, 11))
    assert await bench.dispatcher.run_until_idle() == 0  # set aside: kept and alerted, not taken again
    await bench.superuser.execute("DROP TRIGGER refuse_poison ON other.inbox")
    await bench.superuser.execute("UPDATE probe.outbox SET attempts = 0")  # the operator's reset
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.count("other", "inbox") == 3


async def test_a_schema_another_dispatcher_is_fanning_out_is_skipped(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    await bench.publish()
    async with bench.superuser.transaction():
        await bench.superuser.execute(HOLD_PROBE)  # another dispatcher's pass over probe
        assert await bench.dispatcher.run_until_idle() == 0
        assert await bench.count("probe", "outbox") == 1
    assert await bench.dispatcher.run_until_idle() == 1
    assert await bench.count("other", "inbox") == 1


async def test_every_committed_pass_that_moved_rows_signals_the_inboxes(bench: Bench) -> None:
    bench.subscribe("other", RENAMES)
    await bench.publish()
    await bench.superuser.execute(COPY_TO_OTHER)
    assert await bench.dispatcher.run_once() == 2
    assert bench.signals.sent == ["ledger_inbox", "ledger_inbox"]  # one per schema's commit


def test_fan_out_inserts_stay_under_postgresqls_parameter_limit() -> None:
    assert ROWS_PER_INSERT * 13 <= 65_535 < (ROWS_PER_INSERT + 1) * 13
    rows = list(range(2 * ROWS_PER_INSERT + 1))
    assert [len(chunk) for chunk in chunked(rows, ROWS_PER_INSERT)] == [ROWS_PER_INSERT, ROWS_PER_INSERT, 1]
    assert [item for chunk in chunked(rows, ROWS_PER_INSERT) for item in chunk] == rows
    assert list(chunked(list[int](), ROWS_PER_INSERT)) == []


async def test_a_failure_of_every_row_alike_counts_against_none_and_is_raised(bench: Bench) -> None:
    """The relay may not insert into the inbox (a broken grant): no row is to blame, none is set aside.

    A dispatcher whose every schema failed raises (its supervisor backs off and starts it again).
    """
    bench.subscribe("other", RENAMES)
    await bench.publish(2)
    await bench.superuser.execute(COPY_TO_OTHER)  # both producers' events go to the inbox it may not write
    await bench.superuser.execute("REVOKE INSERT ON other.inbox FROM ledger_relay")
    both = Dispatcher(bench.probe.database, schemas=("probe", "other"), signals=bench.signals)
    try:
        with capture_logs() as logs, pytest.raises(ProgrammingError, match="permission denied"):
            await both.run_until_idle()
        assert [entry["schema"] for entry in logs] == ["probe", "other"]
        attempts = "SELECT attempts FROM probe.outbox UNION ALL SELECT attempts FROM other.outbox"
        assert await bench.rows(attempts) == [(0,)] * 4
    finally:
        await bench.superuser.execute("GRANT INSERT ON other.inbox TO ledger_relay")
    assert await both.run_until_idle() == 4
    assert await bench.count("other", "inbox") == 4


async def test_a_failure_that_is_not_the_rows_own_never_blames_it(bench: Bench, poisoned: None) -> None:
    """Another row of the pass went through, but the error is not the row's own: skipped, uncounted."""
    del poisoned
    bench.subscribe("other", RENAMES)
    await bench.publish_named("poison", "fine")
    await bench.superuser.execute(PERMISSION_TRIGGER)
    with capture_logs() as logs:
        assert await bench.dispatcher.run_once() == 1
    assert logs == []
    assert await bench.rows("SELECT payload ->> 'name', attempts FROM probe.outbox") == [("poison", 0)]


async def test_a_schema_whose_pass_fails_is_logged_and_the_others_still_go(
    bench: Bench, registry: CollectorRegistry
) -> None:
    """probe's events go to an inbox the relay may no longer write; other's go elsewhere and are delivered."""
    bench.subscribe("other", RENAMES)
    bench.subscribe("probe", {"probe.echo": frozenset({"probe.thing.created"})})
    await bench.publish()
    await bench.superuser.execute(CREATED_IN_OTHER)
    await bench.superuser.execute("REVOKE INSERT ON other.inbox FROM ledger_relay")
    try:
        with capture_logs() as logs:
            assert await bench.dispatcher.run_once() == 1  # other's event; probe's failed, uncounted
        failed_pass = [entry for entry in logs if entry["event"] == "outbox.pass_failed"]
        assert [(entry["schema"], entry["log_level"]) for entry in failed_pass] == [("probe", "warning")]
        assert _TEXT.validate_python(failed_pass[0]["error"]).startswith("sqlalchemy.exc.")
        assert registry.get_sample_value("ledger_outbox_pass_failed_total", {"schema": "probe"}) == 1
        assert await bench.rows("SELECT consumer FROM probe.inbox") == [("probe.echo",)]
        assert await bench.rows("SELECT attempts FROM probe.outbox") == [(0,)]
    finally:
        await bench.superuser.execute("GRANT INSERT ON other.inbox TO ledger_relay")
    assert await bench.dispatcher.run_until_idle() == 1
