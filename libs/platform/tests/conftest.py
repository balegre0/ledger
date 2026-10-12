"""Fixtures of the platform's tests: a real PostgreSQL and a real NATS server with JetStream."""

import asyncio
import logging
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator

import psycopg
import pytest
import structlog
from nats.aio.client import Client
from nats.js import JetStreamContext
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from ledger_platform.database import Database
from ledger_platform.schema import CHASSIS_METADATA
from ledger_platform.tracing import Tracing

POSTGRES_IMAGE = "postgres:18.6"
NATS_IMAGE = "nats:2.12.15"
NATS_PORT = 4222
SMALL_PAYLOAD_CONFIG = "/small-payload.conf"


def use_the_plain_baseline() -> None:
    """structlog as a service logs to a terminal: no colors, and tracebacks as plain text without locals.

    structlog's own defaults render a traceback with ``rich`` and every local variable of every frame, which
    is slow (seconds for one logged exception) and prints exactly what ``configure_logging`` keeps out of a
    record. A test that logs gets the production rules, and ``capture_logs`` keeps working on top of them.
    """
    structlog.reset_defaults()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False, exception_formatter=structlog.dev.plain_traceback),
        ],
        cache_logger_on_first_use=False,
    )


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: ``configure_logging`` is process-wide state."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    use_the_plain_baseline()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    use_the_plain_baseline()
    structlog.contextvars.clear_contextvars()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A real PostgreSQL; the app connects as ``ledger_app``, which owns the database but is no superuser.

    A superuser bypasses row-level security, and so does a table owner unless the table forces it: the tests
    must run as the kind of role a service runs as.
    """
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        with psycopg.connect(container.get_connection_url(), autocommit=True) as admin:
            admin.execute("CREATE ROLE ledger_app LOGIN PASSWORD 'app-password'")
            admin.execute("CREATE DATABASE ledger OWNER ledger_app")
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        yield f"postgresql+psycopg://ledger_app:app-password@{host}:{port}/ledger"


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """The chassis's tables, created for one test and dropped after it."""
    async with Database.open(database_url, pool_size=2) as opened:
        async with opened.engine.begin() as connection:
            await connection.run_sync(CHASSIS_METADATA.create_all)
        yield opened
        async with opened.engine.begin() as connection:
            await connection.run_sync(CHASSIS_METADATA.drop_all)


def nats_server(*arguments: str) -> DockerContainer:
    """A NATS server with JetStream, started with ``arguments`` too, ready once it says so."""
    return (
        DockerContainer(NATS_IMAGE)
        .with_command(["-js", *arguments])
        .with_exposed_ports(NATS_PORT)
        .waiting_for(LogMessageWaitStrategy("Server is ready"))
    )


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(NATS_PORT)}"


@pytest.fixture(scope="session")
def small_payload_nats_url() -> Iterator[str]:
    """A real NATS server with JetStream whose messages are at most 64 KiB, headers included.

    ``max_payload`` is set in a configuration file: the server takes no flag for it.
    """
    server = nats_server("--config", SMALL_PAYLOAD_CONFIG).with_copy_into_container(
        b"max_payload: 65536\n", SMALL_PAYLOAD_CONFIG
    )
    with server as container:
        yield f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(NATS_PORT)}"


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
    """Waits until ``check`` is true, for at most five seconds: a bound, it returns as soon as it holds.

    For what happens in a task of its own, such as a relay or a consumer, whose progress a test cannot await.
    """

    async def wait(check: Callable[[], Awaitable[bool]]) -> None:
        async with asyncio.timeout(5):
            while True:
                if await check():
                    return
                await asyncio.sleep(0.02)

    return wait
