"""Fixtures of the platform's tests: a real PostgreSQL and a real NATS server with JetStream."""

import logging
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator

import psycopg
import pytest
import structlog
from nats.aio.client import Client
from nats.js import JetStreamContext
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from ledger_platform.database import Database, PlatformTable

POSTGRES_IMAGE = "postgres:18.6"
NATS_IMAGE = "nats:2.12.15"
NATS_PORT = 4222


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
            await connection.run_sync(PlatformTable.metadata.create_all)
        yield opened
        async with opened.engine.begin() as connection:
            await connection.run_sync(PlatformTable.metadata.drop_all)


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    server = (
        DockerContainer(NATS_IMAGE)
        .with_command("-js")
        .with_exposed_ports(NATS_PORT)
        .waiting_for(LogMessageWaitStrategy("Server is ready"))
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
