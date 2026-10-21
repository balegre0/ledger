"""Fixtures of the platform's tests: databases on the run's PostgreSQL (``ledger_testing``'s)."""

from typing import Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping

import psycopg
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
lazy from sqlalchemy import Engine

from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.migrations import PLATFORM_MIGRATIONS
from ledger_platform.data.roles import SchemaRoles, identifier
from ledger_platform.events.subscriptions import bump_routing, sync_subscriptions
from ledger_platform.observability.tracing import Tracing
from ledger_testing import waiting
from ledger_testing.database import app_url, bootstrap, create_chassis, migrate, migrator_url
lazy from ledger_platform.data.notifier import Channel, Signals
lazy from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase

SCHEMAS = ("platform", "probe", "other")
"""The platform's schema, and two test schemas with the chassis's tables: one unit's and another module's."""
ISOLATED: Final = "vault"
"""The schema of a test module isolated in the signer's role, its roles ``ledger_signer``'s. Only the roles
are the template's: the tests of the signer's role make databases of their own with it."""


def pytest_ledger_template(template: Template) -> None:
    """The roles, the platform's schema migrated, and the chassis's tables in ``probe`` and ``other``."""
    bootstrap(template, (*SCHEMAS, ISOLATED), isolated=(ISOLATED,))
    migrate(template, PLATFORM_MIGRATIONS)
    create_chassis(template, "probe")
    create_chassis(template, "other")


@pytest.fixture
def chassis(worker_database: WorkerDatabase) -> Iterator[WorkerDatabase]:
    """This worker's database with the chassis's schemas; every row a test wrote is removed after it."""
    yield worker_database
    worker_database.truncate(SCHEMAS)


@pytest.fixture(scope="session")
def database_url(postgres: PostgresServer, worker_name: str) -> str:
    """A plain database of this worker's, apart from the template's: for the tests of ``Database`` itself.

    It connects as a role that owns the database but is no superuser.
    """
    return postgres.database(
        f"ledger_{worker_name}_plain", role=f"ledger_plain_{worker_name}", password="plain-password"
    )


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool of two on ``database_url``'s plain database, closed after the test."""
    async with Database.open(database_url, pool_size=2) as opened:
        yield opened


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    """Where the spans of ``tracing`` end up, for a test to read."""
    return InMemorySpanExporter()


@pytest.fixture
def tracing(exporter: InMemorySpanExporter) -> Tracing:
    """A tracer provider of the test's own; every finished span goes to ``exporter``."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(provider)


@pytest.fixture
def registry() -> CollectorRegistry:
    """A metrics registry of the test's own, so that no two tests see each other's counters."""
    return CollectorRegistry()


@pytest.fixture
def eventually() -> Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]:
    """Waits until ``check`` is true, for at most five seconds (``ledger_testing.waiting.eventually``).

    For what happens in a task of its own, such as a runner, whose progress a test cannot await.
    """
    return waiting.eventually


# ── Eventing (§3.5): the app's pool, the migrator, two bound modules, subscriptions ──────────────────────

RENAMED: Final = "probe.thing.renamed"
CREATED: Final = "probe.thing.created"

type Subscribe = Callable[[str, Mapping[str, frozenset[str]]], int]
"""``subscribe(schema, {consumer: types})``: what migrate does for one schema; the new routing version."""


@final
class _Recorded:
    """``Signals`` that write down what they were told, in order."""

    __slots__ = ("_sent",)

    def __init__(self, sent: list[Channel]) -> None:
        self._sent = sent

    def signal(self, channel: Channel) -> None:
        self._sent.append(channel)


@pytest.fixture
async def pool(chassis: WorkerDatabase) -> AsyncGenerator[Database]:
    """Four connections as ``ledger_app`` (``chassis`` empties the schemas after the test)."""
    opened = Database.create(app_url(chassis), pool_size=4)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
async def superuser(chassis: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session for a test's setup and checks; it puts ``platform.routing`` back to 0 after."""
    async with await psycopg.AsyncConnection.connect(chassis.admin_url(), autocommit=True) as connection:
        yield connection
        await connection.execute("UPDATE platform.routing SET version = 0")


@pytest.fixture
def migrator(chassis: WorkerDatabase) -> Iterator[Engine]:
    """``ledger_migrator``, as ``ledger-migrate`` connects (synchronously)."""
    engine = create_engine(migrator_url(chassis), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def subscribe(migrator: Engine, superuser: psycopg.AsyncConnection) -> Subscribe:
    """Sync a schema's subscriptions as its owner, then bump the routing version as ``platform_owner``."""
    del superuser  # requested for its cleanup: the routing version goes back to 0

    def run(schema: str, declared: Mapping[str, frozenset[str]]) -> int:
        with migrator.begin() as connection:
            connection.execute(text(f"SET LOCAL ROLE {identifier(SchemaRoles(schema=schema).owner)}"))
            sync_subscriptions(connection, schema, declared)
        with migrator.begin() as connection:
            connection.execute(text("SET LOCAL ROLE platform_owner"))
            return bump_routing(connection)

    return run


@pytest.fixture
def sent() -> list[Channel]:
    """The channels ``signals`` was told about, in order."""
    return []


@pytest.fixture
def signals(sent: list[Channel]) -> Signals:
    """Signals that only record (``sent``)."""
    return _Recorded(sent)


@pytest.fixture
def probe(pool: Database, signals: Signals) -> BoundDatabase:
    """The producer: it publishes ``probe.thing.renamed`` and ``probe.thing.created``."""
    return BoundDatabase(
        pool, schema="probe", source="probe", publishes=frozenset({RENAMED, CREATED}), signals=signals
    )


@pytest.fixture
def other(pool: Database, signals: Signals) -> BoundDatabase:
    """Another module: it publishes nothing and consumes."""
    return BoundDatabase(pool, schema="other", source="other", signals=signals)
