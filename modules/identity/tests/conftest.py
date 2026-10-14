"""identity's tests: the worker's database migrated as production migrates it, and identity served by ``api``.

The template holds the platform's schema and identity's, each brought to head by its revisions as
``ledger_migrator`` with the relay's grants (what ``ledger-migrate`` runs; the migrate app itself is tested on
fresh databases in ``test_migrations.py``). Tests reach identity's schema the way the apps do: as
``ledger_app``, through a ``BoundDatabase`` in ``identity_rw``.
"""

from typing import Final, final
lazy from collections.abc import AsyncGenerator, Iterator

import httpx
import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr

from ledger_identity._manifest import MANIFEST
from ledger_identity.adapters.postgres.schema import MIGRATIONS
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_platform.config.settings import Settings
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.database import Database
from ledger_platform.data.migrations import PLATFORM_MIGRATIONS
from ledger_platform.data.roles import APP, MIGRATOR
from ledger_platform.runtime.app import App
from ledger_testing.api import TestPrincipal, created
from ledger_testing.database import TEST_LOGINS, app_url, bootstrap, fresh_database, migrate, migrator_url
lazy from ledger_platform.data.notifier import Channel, Signals
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase

SCHEMAS: Final = ("platform", "identity")
TEST_ACTOR: Final = Actor("system:test", "identity-tests")
"""Who the use cases a test calls itself act as; a request through the app acts as its caller instead."""
COMPANY: Final = {"tax_id": {"scheme": "do.rnc", "value": "101850043"}, "legal_name": "Duarte"}
"""What the ``company`` fixture registers."""
PUBLISHES: Final = frozenset(event.type for event in MANIFEST.events.publishes)


def pytest_ledger_template(template: Template) -> None:
    """The roles and the schemas ``platform`` and ``identity``, each at head, the relay granted."""
    bootstrap(template, SCHEMAS)
    migrate(template, PLATFORM_MIGRATIONS, MIGRATIONS)


@final
class _Quiet:
    """``Signals`` that tell no one: identity's tests run no worker to wake (a test may record them)."""

    __slots__ = ()

    def signal(self, channel: Channel) -> None:
        del channel


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own calls of the use cases audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


@pytest.fixture
def migrated(worker_database: WorkerDatabase) -> Iterator[WorkerDatabase]:
    """This worker's database; every row a test left in identity's schema is removed after it."""
    yield worker_database
    worker_database.truncate(("identity",))


@pytest.fixture
async def database(migrated: WorkerDatabase) -> AsyncGenerator[Database]:
    """A pool of four connections as ``ledger_app``."""
    opened = Database.create(app_url(migrated), pool_size=4)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
def signals() -> Signals:
    """Where identity's units signal their commits."""
    return _Quiet()


@pytest.fixture
def identity_db(database: Database, signals: Signals) -> BoundDatabase:
    """identity's units, as the apps make them from its manifest."""
    return BoundDatabase(database, schema="identity", source="identity", signals=signals, publishes=PUBLISHES)


@pytest.fixture
async def admin(migrated: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session, for what a test reads past row-level security (the outbox, every company)."""
    async with await psycopg.AsyncConnection.connect(migrated.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture
def settings(migrated: WorkerDatabase, local_issuer: LocalIssuer) -> Settings:
    """The apps' settings on the worker's database and the test's identity provider."""
    return Settings(
        environment="test",
        database_url=SecretStr(app_url(migrated)),
        database_direct_url=SecretStr(migrator_url(migrated)),
        token_issuer=local_issuer.issuer,
        token_audience=local_issuer.audience,
        token_jwks_url=local_issuer.keys_url,
    )


@pytest.fixture
def api(settings: Settings) -> App:
    """The ``api`` app, identity mounted in it (installed, found by its entry point)."""
    return App("api", settings=settings)


@pytest.fixture
async def client(api: App, database: Database) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of the ``api`` app, started; ``migrated`` empties identity's tables once it has stopped."""
    del database  # requested so that it closes after the app: the tables are emptied last
    served = api.asgi()
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as opened:
            yield opened


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator (``ORGANIZATION_ADMIN``):
    it owns every company it registers."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    """The id of a company ``owner`` registered (``COMPANY``, with the key ``c-1``, in test mode)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("c-1"))


@pytest.fixture
def fresh_database_name(worker_database: WorkerDatabase, postgres: PostgresServer) -> Iterator[str]:
    """A database of the test's own with the schemas ``platform`` and ``identity``, empty; then dropped."""
    del worker_database  # its template made the roles a fresh database's schemas are owned by
    name = fresh_database(postgres, SCHEMAS)
    yield name
    with psycopg.connect(postgres.libpq(), autocommit=True) as connection:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def fresh(fresh_database_name: str, postgres: PostgresServer, local_issuer: LocalIssuer) -> Settings:
    """The apps' settings on a fresh database: units as ``ledger_app``, migrate as ``ledger_migrator``."""
    return Settings(
        environment="test",
        database_url=SecretStr(postgres.sqlalchemy(fresh_database_name, role=APP, password=TEST_LOGINS.app)),
        database_direct_url=SecretStr(
            postgres.sqlalchemy(fresh_database_name, role=MIGRATOR, password=TEST_LOGINS.migrator)
        ),
        token_issuer=local_issuer.issuer,
        token_audience=local_issuer.audience,
        token_jwks_url=local_issuer.keys_url,
    )


@pytest.fixture
def fresh_admin_url(fresh_database_name: str, postgres: PostgresServer) -> str:
    """The fresh database as the superuser (SQLAlchemy URL), for what a test reads of its catalog."""
    return postgres.sqlalchemy(fresh_database_name, role=postgres.user, password=postgres.password)
