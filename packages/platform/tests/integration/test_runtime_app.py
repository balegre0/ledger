"""``App`` running (§3.3, §3.10): the ``api`` and ``worker`` roles, and ``migrate`` (Review Focus 5).

Every test gets a database of its own with the schemas ``platform``, ``probe`` and ``other`` and nothing in
them, and the modules ``probe`` (a route, a consumer, a job kind, a schedule and one revision, written to
``tmp_path``) and ``other`` (a schema and its revision, nothing else).
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import Annotated, ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
lazy from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import APIRouter, Depends
from psycopg import sql
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import text
lazy from fastapi import FastAPI

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import load_manifest
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.data.roles import APP, MIGRATOR
from ledger_platform.events.consumers import ConsumerSpec
from ledger_platform.jobs.outcomes import Done, JobSpec
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import Module, ModuleParts
from ledger_platform.runtime.registries import MODULES, Discovered
from ledger_testing.database import TEST_LOGINS, fresh_database
from ledger_testing.issuer import SUBJECT
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.data.unit_of_work import UnitOfWork
lazy from ledger_platform.events.consumers import RawEvent
lazy from ledger_platform.jobs.outcomes import Job, JobOutcome
lazy from ledger_platform.runtime.contexts import ModuleContext
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import PostgresServer, WorkerDatabase

type Eventually = Callable[[Callable[[], Awaitable[bool]]], Awaitable[None]]
"""The ``eventually`` fixture of conftest.py (basedpyright cannot resolve an import of a conftest)."""

TENANT: Final = Tenant(OrganizationId(uuid.uuid7()), CompanyId(uuid.uuid7()), Mode.TEST)
CHECKS: Final = TypeAdapter(dict[str, bool])
PATHS: Final = TypeAdapter(dict[str, object])
ENV: Final = "from ledger_platform.data.migrations import run_schema_migrations\n\nrun_schema_migrations()\n"
REVISION: Final = """from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1

revision = "r0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    chassis_schema_v1(op, "{schema}")
"""
MANIFEST: Final = """[package]
name = "probe"
kind = "module"
summary = "A module for the chassis's tests."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[data]
schema = "probe"

[[events.publishes]]
type = "probe.thing.renamed"
version = 1

[[jobs]]
kind = "probe.export"

[[schedules]]
name = "probe.nightly"
every = "24h"
enqueues = "probe.export"

[http]
mounted_in = ["api"]
prefixes = ["/v1/probes"]
"""
CONSUMES: Final = """
[[events.consumes]]
consumer = "probe.echo"
types = ["probe.thing.renamed"]
"""
OTHER: Final = """[package]
name = "other"
kind = "module"
summary = "A module with a schema and nothing else: no route, no consumer, no job."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
modules = ["probe"]

[data]
schema = "other"
"""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


@final
class ProbeModule:
    """The modules ``probe`` and ``other``; ``handled`` and ``exported``: what probe's consumer and job saw.

    ``consumes = False`` drops the consumer from probe's manifest and its assembly (a module that stopped
    consuming).
    """

    __slots__ = ("consumes", "exported", "handled", "opened", "scripts")

    def __init__(self, scripts: str) -> None:
        self.scripts = scripts
        self.handled: list[RawEvent] = []
        self.exported: list[str] = []
        self.consumes = True
        self.opened: list[str] = []
        """What happened to its resource: ``open``, then ``closed``."""

    def discover(self) -> Discover:
        """Discovery that finds this module and nothing else."""
        manifest = load_manifest(MANIFEST + (CONSUMES if self.consumes else ""))
        module = Module(manifest=manifest, assemble=self._assemble)
        other = Module(manifest=load_manifest(OTHER), assemble=self._other)
        found = (Discovered("probe", partial(_same, module)), Discovered("other", partial(_same, other)))

        def discover(group: str) -> Sequence[Discovered]:
            return found if group == MODULES else ()

        return discover

    def _assemble(self, context: ModuleContext) -> ModuleParts:
        router = APIRouter(prefix="/v1/probes")
        caller = context.auth.caller

        @router.get("/ping")
        async def ping(principal: Annotated[Principal, Depends(caller)]) -> dict[str, str]:
            return {"pong": principal.subject}

        consumers = (ConsumerSpec(name="probe.echo", handler=self._record),) if self.consumes else ()
        return ModuleParts(
            routers=(router,),
            checks={"ready": self._ready},
            resources=(self._resource(),),
            consumers=consumers,
            jobs=(JobSpec(kind="probe.export", handler=self._export),),
            migrations=SchemaMigrations(schema="probe", scripts=f"{self.scripts}/probe"),
        )

    def _other(self, context: ModuleContext) -> ModuleParts:
        del context
        return ModuleParts(migrations=SchemaMigrations(schema="other", scripts=f"{self.scripts}/other"))

    async def _ready(self) -> None:
        """Ready while its resource is open."""
        assert self.opened == ["open"]

    @asynccontextmanager
    async def _resource(self) -> AsyncGenerator[None]:
        self.opened.append("open")
        yield
        self.opened.append("closed")

    async def _record(self, event: RawEvent, unit: UnitOfWork) -> None:
        del unit
        self.handled.append(event)

    async def _export(self, job: Job) -> JobOutcome:
        self.exported.append(job.key)
        return Done()


def _same(value: object) -> object:
    return value


@final
@dataclass(frozen=True, slots=True)
class Fresh:
    """A new database with the schemas ``platform``, ``probe`` and ``other``, empty (the roles exist)."""

    server: PostgresServer
    name: str

    @property
    def app_url(self) -> str:
        return self.server.sqlalchemy(self.name, role=APP, password=TEST_LOGINS.app)

    @property
    def migrator_url(self) -> str:
        return self.server.sqlalchemy(self.name, role=MIGRATOR, password=TEST_LOGINS.migrator)

    def admin(self) -> psycopg.Connection:
        """A superuser session, for a test's checks."""
        return psycopg.connect(self.server.libpq(self.name), autocommit=True)

    def rows(self, query: LiteralString) -> list[tuple[object, ...]]:
        with self.admin() as connection:
            return [tuple(row) for row in connection.execute(query).fetchall()]

    def settings(
        self, *, direct: str | None = None, issuer: LocalIssuer | None = None, pooled: bool = False
    ) -> Settings:
        """An app's settings on this database as ``ledger_app``; ``direct`` for LISTEN or migrate."""
        return Settings(
            environment="test",
            database_url=SecretStr(self.app_url),
            database_direct_url=None if direct is None else SecretStr(direct),
            token_issuer=None if issuer is None else issuer.issuer,
            token_audience=None if issuer is None else issuer.audience,
            token_jwks_url=None if issuer is None else issuer.keys_url,
            database_pooled=pooled,
        )

    def migrate(self, probe: ProbeModule) -> None:
        App("migrate", settings=self.settings(direct=self.migrator_url), discover=probe.discover()).migrate()


@pytest.fixture
def probe(tmp_path: Path) -> ProbeModule:
    """The modules ``probe`` and ``other``, their revisions written to ``tmp_path``."""
    for schema in ("probe", "other"):
        versions = tmp_path / schema / "versions"
        versions.mkdir(parents=True)
        (tmp_path / schema / "env.py").write_text(ENV)
        (versions / f"r0001_{schema}.py").write_text(REVISION.format(schema=schema))
    return ProbeModule(str(tmp_path))


@pytest.fixture
def fresh(chassis: WorkerDatabase, postgres: PostgresServer) -> Iterator[Fresh]:
    """The roles come from the template (``chassis``): a fresh database gets only its schemas."""
    del chassis
    name = fresh_database(postgres, ("platform", "probe", "other"))
    yield Fresh(postgres, name)
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    """``app`` started (its lifespan), and a client of it."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            yield client


# ── api ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_api_serves_its_modules_routes_to_proven_callers(
    fresh: Fresh, probe: ProbeModule, local_issuer: LocalIssuer
) -> None:
    await asyncio.to_thread(fresh.migrate, probe)
    app = App("api", settings=fresh.settings(issuer=local_issuer), discover=probe.discover())
    async with serving(app.asgi()) as client:
        bearer = {"Authorization": f"Bearer {local_issuer.token()}"}
        pong = await client.get("/v1/probes/ping", headers=bearer)
        anonymous = await client.get("/v1/probes/ping")
        ready = await client.get("/readyz")
        live = await client.get("/livez")
        document = await client.get("/openapi.json")
    assert (pong.status_code, pong.json()) == (HTTPStatus.OK, {"pong": SUBJECT})
    assert len(pong.headers["Request-Id"]) == 32  # every traced response names its request
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    assert ready.status_code == HTTPStatus.OK
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:probe": True,
        "schema:other": True,
        "probe:ready": True,
    }
    assert live.status_code == HTTPStatus.OK
    assert probe.opened == ["open", "closed"]
    assert set(PATHS.validate_python(document.json()["paths"])) == {"/v1/probes/ping"}


async def test_the_api_is_not_ready_on_a_database_never_migrated(
    fresh: Fresh, probe: ProbeModule, local_issuer: LocalIssuer
) -> None:
    app = App("api", settings=fresh.settings(issuer=local_issuer), discover=probe.discover())
    async with serving(app.asgi()) as client:
        ready = await client.get("/readyz")
        live = await client.get("/livez")
    assert ready.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": False,
        "schema:probe": False,
        "schema:other": False,
        "probe:ready": True,
    }
    assert live.status_code == HTTPStatus.OK


async def prepared_statements(app: App) -> int:
    """How many statements a pooled connection of ``app`` prepared after running one six times."""
    async with app.bound("probe").database.engine.connect() as connection:
        for _ in range(6):
            await connection.execute(text("SELECT 1"))
        prepared = await connection.execute(text("SELECT count(*) FROM pg_prepared_statements"))
        return TypeAdapter(int).validate_python(prepared.scalar_one())


async def test_a_pool_reached_through_a_pooler_prepares_no_statement(
    fresh: Fresh, probe: ProbeModule
) -> None:
    """``LEDGER_DATABASE_POOLED``: the database URL goes through a transaction pooler (§6.7)."""
    direct = App("worker", settings=fresh.settings(direct=fresh.app_url), discover=probe.discover())
    pooled = App(
        "worker", settings=fresh.settings(direct=fresh.app_url, pooled=True), discover=probe.discover()
    )
    try:
        assert await prepared_statements(direct) > 0
        assert await prepared_statements(pooled) == 0
    finally:
        await direct.bound("probe").database.aclose()
        await pooled.bound("probe").database.aclose()


# ── worker ──────────────────────────────────────────────────────────────────────────────────────────


async def test_the_worker_fans_out_handles_events_runs_jobs_and_fires_schedules(
    fresh: Fresh, probe: ProbeModule, eventually: Eventually
) -> None:
    await asyncio.to_thread(fresh.migrate, probe)
    app = App("worker", settings=fresh.settings(direct=fresh.app_url), discover=probe.discover())
    served = app.asgi()
    assert app.asgi() is served  # one served app, one lifespan
    before = asyncio.all_tasks()

    async def handled() -> bool:
        return [event.type for event in probe.handled] == ["probe.thing.renamed"]

    async def exported() -> bool:
        return "export-1" in probe.exported and any(
            key.startswith("probe.nightly@") for key in probe.exported
        )

    async def done() -> bool:
        return fresh.rows("SELECT status FROM probe.jobs WHERE key = 'export-1'") == [("done",)]

    async with serving(served) as client:
        async with app.bound("probe").unit(TENANT) as unit:
            unit.publish(Renamed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name="renamed"))
            unit.jobs.enqueue("probe.export", "export-1")
        await eventually(handled)
        await eventually(exported)
        await eventually(done)
        ready = await client.get("/readyz")
        live = await client.get("/livez")
        document = await client.get("/openapi.json")
    assert ready.status_code == HTTPStatus.OK
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:probe": True,
        "schema:other": True,
        "probe:ready": True,
        "listen": True,
        "workers": True,
    }
    assert live.status_code == HTTPStatus.OK
    assert PATHS.validate_python(document.json()["paths"]) == {}  # the worker mounts no module's routes
    assert asyncio.all_tasks() - before == set()  # every worker stopped with the app
    assert fresh.rows("SELECT count(*) FROM probe.jobs WHERE status = 'running'") == [(0,)]
    assert fresh.rows("SELECT count(*) FROM probe.inbox WHERE status = 'running'") == [(0,)]


async def test_the_worker_is_not_ready_until_it_listens(fresh: Fresh, probe: ProbeModule) -> None:
    app = App("worker", settings=fresh.settings(direct=fresh.app_url), discover=probe.discover())
    served = app.asgi()
    transport = httpx.ASGITransport(served)
    async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
        ready = await client.get("/readyz")  # outside the lifespan: nothing listens, no worker runs
    checks = CHECKS.validate_python(ready.json()["checks"])
    assert (checks["listen"], checks["workers"]) == (False, False)
    await app.bound("probe").database.aclose()


# ── migrate ─────────────────────────────────────────────────────────────────────────────────────────


def test_migrate_brings_every_schema_to_head_grants_the_relay_and_writes_the_subscriptions(
    fresh: Fresh, probe: ProbeModule
) -> None:
    fresh.migrate(probe)
    assert fresh.rows("SELECT version_num FROM platform.alembic_version") == [("0001",)]
    assert fresh.rows("SELECT version_num FROM probe.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT version_num FROM other.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT consumer, event_type, active FROM probe.subscriptions") == [
        ("probe.echo", "probe.thing.renamed", True)
    ]
    assert fresh.rows("SELECT version FROM platform.routing") == [(1,)]
    with fresh.admin() as connection, connection.transaction():
        connection.execute("SET LOCAL ROLE ledger_relay")
        assert connection.execute("SELECT count(*) FROM probe.outbox").fetchone() == (0,)


def test_migrate_again_changes_nothing_but_the_routing_version(fresh: Fresh, probe: ProbeModule) -> None:
    fresh.migrate(probe)
    fresh.migrate(probe)
    assert fresh.rows("SELECT version_num FROM probe.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT version_num FROM other.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT consumer, event_type, active FROM probe.subscriptions") == [
        ("probe.echo", "probe.thing.renamed", True)
    ]
    assert fresh.rows("SELECT version FROM platform.routing") == [(2,)]


def test_two_migrations_at_once_serialize(fresh: Fresh, probe: ProbeModule) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        for run in [pool.submit(fresh.migrate, probe) for _ in range(2)]:
            run.result()
    assert fresh.rows("SELECT version_num FROM platform.alembic_version") == [("0001",)]
    assert fresh.rows("SELECT version_num FROM probe.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT version_num FROM other.alembic_version") == [("r0001",)]
    assert fresh.rows("SELECT consumer, event_type, active FROM probe.subscriptions") == [
        ("probe.echo", "probe.thing.renamed", True)
    ]
    assert fresh.rows("SELECT version FROM platform.routing") == [(2,)]


def test_a_removed_consumer_is_deactivated_not_deleted(fresh: Fresh, probe: ProbeModule) -> None:
    fresh.migrate(probe)
    probe.consumes = False
    fresh.migrate(probe)
    assert fresh.rows("SELECT consumer, event_type, active FROM probe.subscriptions") == [
        ("probe.echo", "probe.thing.renamed", False)
    ]
