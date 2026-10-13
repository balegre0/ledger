"""Fixtures of the identity service's tests: a real PostgreSQL migrated to this version's schema."""

from typing import Final
lazy from collections.abc import AsyncGenerator, Callable, Iterator

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import SecretStr
from sqlalchemy import text

from ledger_identity.adapters.postgres.migrate import MIGRATIONS
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.main import build
from ledger_identity.settings import IdentitySettings
from ledger_kernel.ids import new_id
from ledger_platform.database import Database
from ledger_platform.outbox import Outbox
from ledger_platform.tracing import Tracing
from ledger_testing.api import Caller, created
from ledger_testing.containers import Postgres, nats_address, nats_server
from ledger_testing.issuer import LocalIssuer
from ledger_testing.logs import restored_logging
lazy from ledger_platform.service import Service

EMPTY_EVERY_TABLE = text(
    """
    TRUNCATE organizations, companies, memberships, branches, emission_points, outbox, inbox, idempotency_keys
    """
)
"""What a test leaves behind is removed: ``TRUNCATE`` is a privilege of the owner, which row-level security
does not filter."""
COMPANY: Final = {"tax_id": "101850043", "legal_name": "Duarte"}
"""What the ``company`` fixture registers."""


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
    MIGRATIONS.upgrade(url)
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


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    """A real NATS server with JetStream."""
    with nats_server() as container:
        yield nats_address(container)


@pytest.fixture
async def issuer() -> AsyncGenerator[LocalIssuer]:
    """The identity provider the service trusts in the tests."""
    async with LocalIssuer.started() as started:
        yield started


@pytest.fixture
def settings_for(nats_url: str, issuer: LocalIssuer) -> Callable[[str], IdentitySettings]:
    """identity's settings on a database: the test's NATS and identity provider, any free gRPC port."""

    def settings(database_url: str) -> IdentitySettings:
        return IdentitySettings(
            environment="test",
            database_url=SecretStr(database_url),
            nats_url=SecretStr(nats_url),
            grpc_port=0,
            token_issuer=issuer.issuer,
            token_audience=issuer.audience,
            token_jwks_url=issuer.keys_url,
        )

    return settings


@pytest.fixture
async def service(
    database: Database, database_url: str, settings_for: Callable[[str], IdentitySettings]
) -> AsyncGenerator[Service[IdentitySettings]]:
    """identity, started on the test's PostgreSQL and NATS; ``database`` empties the tables after it stops."""
    del database
    built = build(settings_for(database_url))
    async with built.app.router.lifespan_context(built.app):
        yield built


@pytest.fixture
async def client(service: Service[IdentitySettings]) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of identity's HTTP app."""
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as opened:
        yield opened


@pytest.fixture
def owner(issuer: LocalIssuer) -> Caller:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator (``ORGANIZATION_ADMIN``):
    it owns every company it registers."""
    return Caller(issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: Caller) -> str:
    """The id of a company ``owner`` registered (``COMPANY``, with the key ``c-1``)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("c-1"))
