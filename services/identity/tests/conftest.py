"""Fixtures of the identity service's tests: a real PostgreSQL migrated to this version's schema."""

lazy from collections.abc import AsyncGenerator, Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from sqlalchemy import text

from ledger_identity.adapters.postgres.migrate import upgrade
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing
from ledger_testing.containers import Postgres
from ledger_testing.logs import restored_logging

EMPTY_EVERY_TABLE = text(
    """
    TRUNCATE organizations, companies, memberships, branches, emission_points, outbox, inbox, idempotency_keys
    """
)
"""What a test leaves behind is removed: ``TRUNCATE`` is a privilege of the owner, which row-level security
does not filter."""


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain baseline: building a service configures logging."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def postgres() -> Iterator[Postgres]:
    """A real PostgreSQL for the whole session."""
    with Postgres.started() as server:
        yield server


@pytest.fixture(scope="session")
def database_url(postgres: Postgres) -> str:
    """identity's database, migrated to this version; the service connects as its owner, no superuser."""
    url = postgres.database("identity", role="identity", password="identity-password")
    upgrade(url)
    return url


@pytest.fixture
def fresh_database_url(postgres: Postgres) -> str:
    """An empty database of its own, owned by a role of its own: for the tests of the migrations."""
    name = f"fresh_{new_id().hex[16:]}"  # the random half of a UUIDv7
    return postgres.database(name, role=name, password="fresh-password")


@pytest.fixture
async def database(database_url: str) -> AsyncGenerator[Database]:
    """A pool on identity's database, every table emptied after the test."""
    async with Database.open(database_url, pool_size=4) as opened:
        yield opened
        async with opened.engine.begin() as connection:
            await connection.execute(EMPTY_EVERY_TABLE)


@pytest.fixture
def outbox() -> Outbox:
    """An outbox of the test's own: its own tracer provider and metrics registry."""
    return Outbox(tracing=Tracing(TracerProvider()), registry=CollectorRegistry())
