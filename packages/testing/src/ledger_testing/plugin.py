"""The ledger's pytest plugin (entry point ``pytest11``): what every package's tests share (§14.2).

- ``postgres``: one PostgreSQL server per run. Under xdist the controller starts it when the first worker is
  configured and hands its address to every worker (``workerinput``); without xdist the session starts one.
- ``worker_database``: this worker's own database, a clone of a template that the packages'
  ``pytest_ledger_template`` hooks prepare once per server.
- ``issuer_keys`` (session) and ``local_issuer`` (per test): an OpenID provider whose RSA key is made once.
- ``localstack`` (session): LocalStack's S3 (``ledger_testing.localstack``), started for the first test of a
  process that asks for it.
- ``clock``: a wall clock the test moves. ``restore_logging`` (autouse): the plain logging baseline.
- ``archive_store`` and ``archive``: an immutable store in memory, and the archive the app would bind over it
  (fiscal-kit's ``ArchivePort``, its retention counted from ``clock``). ``test_signer``: a ``SignerPort`` that
  signs nothing (``ledger_testing.signer``); a pack's conftest overrides it with one that signs.
- A module's fixtures, for a suite whose conftest returns its ``ModuleUnderTest`` from ``module_under_test``
  (``ledger_testing.modules``): ``migrated``, ``database``, ``app_database``, ``signals``,
  ``module_database``, ``admin``, ``settings``, ``api``, ``client``, ``fresh_database_name``, ``fresh``,
  ``fresh_admin_url``. A conftest's own fixture of one of these names wins over the plugin's.
- A selector event loop: psycopg's async driver cannot run on Windows' proactor loop.

pytest imports this module, and what its hook specification names, before coverage starts: the names it uses
only when a fixture runs are imported lazily, so that coverage sees them load.
"""

import asyncio
import os
from abc import ABCMeta, abstractmethod
from contextlib import ExitStack, nullcontext
from typing import Final, Protocol
lazy from collections.abc import AsyncGenerator, Callable, Iterator, Mapping, MutableMapping
lazy from contextlib import AbstractContextManager

import pytest
from pydantic import TypeAdapter
lazy import httpx
lazy import psycopg
lazy from psycopg import sql

from ledger_testing.hooks import LedgerHooks
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.data.roles import APP, MIGRATOR
lazy from ledger_platform.runtime.app import App
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_testing.blobs import MemoryImmutableStore
lazy from ledger_testing.clock import ManualClock
lazy from ledger_testing.database import TEST_LOGINS, app_url, fresh_database, migrator_url, signer_url
lazy from ledger_testing.issuer import IssuerKeys, LocalIssuer
lazy from ledger_testing.localstack import LocalStack, started_localstack
lazy from ledger_testing.logs import restored_logging
lazy from ledger_testing.modules import ModuleUnderTest, QuietSignals, app_settings
lazy from ledger_testing.postgres import (
    TEMPLATE,
    PostgresServer,
    Template,
    WorkerDatabase,
    clone,
    ensure_template,
    started_postgres,
)
lazy from ledger_testing.signer import TestSigner

_SERVER_INPUT: Final = "ledger_postgres"
_WORKER_INPUT: Final = TypeAdapter(dict[str, object])
_ADDRESS: Final = pytest.StashKey[str]()
"""The controller's server, as its workers receive it (``PostgresServer.to_json``)."""
_STACK: Final = pytest.StashKey[ExitStack]()


class Node(Protocol, metaclass=ABCMeta):
    """What ``pytest_configure_node`` uses of xdist's ``WorkerController``."""

    @property
    @abstractmethod
    def config(self) -> pytest.Config:
        """The controller's configuration."""

    @property
    @abstractmethod
    def workerinput(self) -> MutableMapping[str, object]:
        """What the worker receives at its start."""


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Declare ``pytest_ledger_template``."""
    pluginmanager.add_hookspecs(LedgerHooks)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """A selector event loop everywhere: Windows' default proactor loop cannot run psycopg's async driver."""
    del config, item
    return {"selector": asyncio.SelectorEventLoop}


def _controller_address(config: pytest.Config) -> str:
    if _ADDRESS not in config.stash:
        stack = ExitStack()
        config.stash[_STACK] = stack
        config.stash[_ADDRESS] = stack.enter_context(started_postgres()).to_json()
    return config.stash[_ADDRESS]


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Node) -> None:
    """For each worker, on xdist's controller: the one server, started for the first worker."""
    node.workerinput[_SERVER_INPUT] = _controller_address(node.config)


def pytest_unconfigure(config: pytest.Config) -> None:
    """The controller stops the server it started."""
    stack = config.stash.get(_STACK, None)
    if stack is not None:
        stack.close()


@pytest.fixture(scope="session")
def postgres(request: pytest.FixtureRequest) -> Iterator[PostgresServer]:
    """The run's PostgreSQL server (see the module)."""
    given = _WORKER_INPUT.validate_python(getattr(request.config, "workerinput", {})).get(_SERVER_INPUT)
    opened: AbstractContextManager[PostgresServer] = (
        nullcontext(PostgresServer.from_json(given)) if isinstance(given, str) else started_postgres()
    )
    with opened as server:
        yield server


@pytest.fixture(scope="session")
def worker_name() -> str:
    """``main`` without xdist, else the worker's id (``gw0``, ``gw1``, ...)."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


@pytest.fixture(scope="session")
def worker_database(
    postgres: PostgresServer, worker_name: str, request: pytest.FixtureRequest
) -> WorkerDatabase:
    """This worker's own database, cloned from the template the packages' hooks prepared."""

    def prepare(template: Template) -> None:
        request.config.hook.pytest_ledger_template(template=template)

    ensure_template(postgres, prepare, name=TEMPLATE)
    name = f"ledger_{worker_name}"
    clone(postgres, name, template=TEMPLATE)
    return WorkerDatabase(postgres, name)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Each test starts from, and leaves, the plain logging baseline (logging is process-wide state)."""
    with restored_logging():
        yield


@pytest.fixture(scope="session")
def issuer_keys() -> IssuerKeys:
    """The local OpenID provider's first RSA key, made once per session (§14.2)."""
    return IssuerKeys.generate()


@pytest.fixture
async def local_issuer(issuer_keys: IssuerKeys) -> AsyncGenerator[LocalIssuer]:
    """An OpenID provider on 127.0.0.1 signing with the session's key."""
    async with LocalIssuer.started(keys=issuer_keys) as started:
        yield started


@pytest.fixture(scope="session")
def localstack() -> Iterator[LocalStack]:
    """LocalStack's S3, one container for the process.

    Under xdist each worker that asks starts its own: a package's S3 tests group themselves on one worker
    (``xdist_group("localstack")``).
    """
    with started_localstack() as started:
        yield started


@pytest.fixture
def clock() -> ManualClock:
    """A wall clock the test moves (``Clock``)."""
    return ManualClock()


@pytest.fixture
def archive_store() -> MemoryImmutableStore:
    """An immutable store in memory, empty."""
    return MemoryImmutableStore()


@pytest.fixture
def archive(archive_store: MemoryImmutableStore, clock: ManualClock) -> StoreArchive:
    """The archive over ``archive_store``, as the app binds it: ten years' retention from ``clock``."""
    return StoreArchive(archive_store, retention_years=10, clock=clock)


@pytest.fixture
def test_signer() -> TestSigner:
    """A ``SignerPort`` of the test's own: it signs nothing and its certificate covers any moment."""
    return TestSigner()


# ── A module's fixtures (``ledger_testing.modules``) ────────────────────────────


@pytest.fixture
def migrated(worker_database: WorkerDatabase, module_under_test: ModuleUnderTest) -> Iterator[WorkerDatabase]:
    """This worker's database; every row a test left in the module's schema is removed after it."""
    yield worker_database
    worker_database.truncate(module_under_test.owned)


@pytest.fixture
async def database(migrated: WorkerDatabase, module_under_test: ModuleUnderTest) -> AsyncGenerator[Database]:
    """A pool of four connections as the module's login.

    ``ledger_signer`` for a module isolated in the signer's role, whose schema no other login reaches; else
    ``ledger_app``.
    """
    url = signer_url(migrated) if module_under_test.is_isolated else app_url(migrated)
    opened = Database.create(url, pool_size=4)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
async def app_database(migrated: WorkerDatabase) -> AsyncGenerator[Database]:
    """A pool of two connections as ``ledger_app``: the login of every app but the signer."""
    opened = Database.create(app_url(migrated), pool_size=2)
    try:
        yield opened
    finally:
        await opened.aclose()


@pytest.fixture
def signals() -> Signals:
    """Where the module's units signal their commits: nobody (a test may record them with its own)."""
    return QuietSignals()


@pytest.fixture
def module_database(
    database: Database, signals: Signals, module_under_test: ModuleUnderTest
) -> BoundDatabase:
    """The module's units, as the apps make them from its manifest."""
    return BoundDatabase(
        database,
        schema=module_under_test.schema,
        source=module_under_test.source,
        signals=signals,
        publishes=module_under_test.publishes,
        jobs=module_under_test.jobs,
    )


@pytest.fixture
async def admin(migrated: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session, for what a test reads past row-level security."""
    async with await psycopg.AsyncConnection.connect(migrated.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture
def settings(migrated: WorkerDatabase, local_issuer: LocalIssuer) -> Settings:
    """The apps' settings on the worker's database and the test's identity provider."""
    return app_settings(
        database_url=app_url(migrated), direct_url=migrator_url(migrated), issuer=local_issuer
    )


@pytest.fixture
def api(settings: Settings, module_under_test: ModuleUnderTest) -> App:
    """The ``api`` app, the module under test and what it depends on mounted in it (``discovering``)."""
    return App("api", settings=settings, discover=module_under_test.discovering())


@pytest.fixture
async def client(api: App, database: Database) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of the ``api`` app, started; ``migrated`` empties the module's tables once it has stopped."""
    del database  # requested so that it closes after the app: the tables are emptied last
    served = api.asgi()
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as opened:
            yield opened


@pytest.fixture
def fresh_database_name(
    worker_database: WorkerDatabase, postgres: PostgresServer, module_under_test: ModuleUnderTest
) -> Iterator[str]:
    """A database of the test's own with the module's schemas, empty, as a first rollout finds it; dropped."""
    del worker_database  # its template made the roles a fresh database's schemas are owned by
    name = fresh_database(postgres, module_under_test.schemas, isolated=module_under_test.isolated)
    yield name
    with psycopg.connect(postgres.libpq(), autocommit=True) as connection:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def fresh(fresh_database_name: str, postgres: PostgresServer, local_issuer: LocalIssuer) -> Settings:
    """The apps' settings on a fresh database: units as ``ledger_app``, migrate as ``ledger_migrator``."""
    return app_settings(
        database_url=postgres.sqlalchemy(fresh_database_name, role=APP, password=TEST_LOGINS.app),
        direct_url=postgres.sqlalchemy(fresh_database_name, role=MIGRATOR, password=TEST_LOGINS.migrator),
        issuer=local_issuer,
    )


@pytest.fixture
def fresh_admin_url(fresh_database_name: str, postgres: PostgresServer) -> str:
    """The fresh database as the superuser (SQLAlchemy URL), for what a test reads of its catalog."""
    return postgres.sqlalchemy(fresh_database_name, role=postgres.user, password=postgres.password)
