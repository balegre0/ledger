"""Fixtures of the platform's tests: a real PostgreSQL and a real NATS server with JetStream."""

lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator

import pytest
from nats.aio.client import Client
from nats.js import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from ledger_kernel.ids import new_id
from ledger_platform.data.database import Database
from ledger_platform.observability.tracing import Tracing
from ledger_platform.schema import CHASSIS_METADATA
from ledger_testing import waiting
from ledger_testing.containers import Postgres, nats_address, nats_server
from ledger_testing.logs import restored_logging

SMALL_PAYLOAD_CONFIG = "/small-payload.conf"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: ``configure_logging`` is process-wide state."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def postgres() -> Iterator[Postgres]:
    """A real PostgreSQL for the whole session."""
    with Postgres.started() as server:
        yield server


@pytest.fixture(scope="session")
def database_url(postgres: Postgres) -> str:
    """The app's database; it connects as ``ledger_app``, which owns the database but is no superuser."""
    return postgres.database("ledger", role="ledger_app", password="app-password")


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"  # the random half of a UUIDv7
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """The chassis's tables, created for one test and dropped after it."""
    async with Database.open(database_url, pool_size=2) as opened:
        async with opened.engine.begin() as connection:
            await connection.run_sync(CHASSIS_METADATA.create_all)
        yield opened
        async with opened.engine.begin() as connection:
            await connection.run_sync(CHASSIS_METADATA.drop_all)


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture(scope="session")
def small_payload_nats_url() -> Iterator[str]:
    """A real NATS server with JetStream whose messages are at most 64 KiB, headers included.

    ``max_payload`` is set in a configuration file: the server takes no flag for it.
    """
    server = nats_server("--config", SMALL_PAYLOAD_CONFIG).with_copy_into_container(
        b"max_payload: 65536\n", SMALL_PAYLOAD_CONFIG
    )
    with server as container:
        yield nats_address(container)


@pytest.fixture
async def connect(nats_url: str) -> AsyncGenerator[Callable[[], Awaitable[Client]]]:
    """Opens connections to NATS; every one a test opened is closed after it.

    ``Client`` and ``JetStreamContext`` are built directly: nats-py's ``connect()`` and ``jetstream()`` take
    ``**options`` without a type, which strict type checking refuses to call.
    """
    opened: list[Client] = []

    async def open_connection() -> Client:
        client = Client()
        await client.connect(nats_url)
        opened.append(client)
        return client

    yield open_connection
    for client in opened:
        await client.close()


@pytest.fixture
async def jetstream(connect: Callable[[], Awaitable[Client]]) -> AsyncGenerator[JetStreamContext]:
    """A JetStream client; every stream a test created is deleted after it."""
    context = JetStreamContext(await connect())
    try:
        yield context
    finally:
        for info in await context.streams_info():
            if info.config.name is not None:
                await context.delete_stream(info.config.name)


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

    For what happens in a task of its own, such as a relay or a consumer, whose progress a test cannot await.
    """
    return waiting.eventually
