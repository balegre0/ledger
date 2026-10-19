"""The ``signer`` role and a module isolated in it, on PostgreSQL (Decision 2; Review Focus 1).

Every test gets a database of its own with the schemas ``platform``, ``probe`` (an ordinary module's) and
``vault`` (a module isolated in the signer's role: ``ledger_signer`` alone enters it), migrated by the
``migrate`` app. ``probe`` publishes ``probe.thing.renamed`` and consumes ``vault.thing.sealed``; ``vault``
publishes ``vault.thing.sealed``, consumes ``probe.thing.renamed`` and runs ``vault.export`` nightly.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import ClassVar, Final, LiteralString, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
lazy from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
lazy from fastapi import FastAPI
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.events import Event
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import load_manifest
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.data.roles import APP, MIGRATOR, SIGNER
from ledger_platform.events.consumers import ConsumerSpec
from ledger_platform.events.dispatcher import Dispatcher
from ledger_platform.jobs.outcomes import Done, JobSpec
from ledger_platform.runtime import app as runtime_app
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import Module, ModuleParts
from ledger_platform.runtime.registries import MODULES, Discovered
from ledger_testing.database import TEST_LOGINS, fresh_database
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.notifier import Signals
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
SCHEMAS: Final = ("platform", "probe", "vault")
ENV: Final = "from ledger_platform.data.migrations import run_schema_migrations\n\nrun_schema_migrations()\n"
REVISION: Final = """from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1, chassis_schema_v2

revision = "r0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    chassis_schema_v1(op, "{schema}")
    chassis_schema_v2(op, "{schema}")
"""
PROBE: Final = """[package]
name = "probe"
kind = "module"
summary = "An ordinary module."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[data]
schema = "probe"

[[events.publishes]]
type = "probe.thing.renamed"
version = 1

[[events.consumes]]
consumer = "probe.sealed"
types = ["vault.thing.sealed"]
"""
VAULT: Final = """[package]
name = "vault"
kind = "module"
summary = "A module isolated in the signer's role."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
modules = ["probe"]

[data]
schema = "vault"
isolated_in = "signer"

[[events.publishes]]
type = "vault.thing.sealed"
version = 1

[[events.consumes]]
consumer = "vault.renamed"
types = ["probe.thing.renamed"]

[[jobs]]
kind = "vault.export"

[[schedules]]
name = "vault.nightly"
every = "24h"
enqueues = "vault.export"
"""
INBOX: Final = """
    INSERT INTO vault.inbox (consumer, event_id, type, version, source, organization_id, company_id, mode,
                             occurred_at, payload)
    VALUES ('vault.renamed', %s, 'probe.thing.renamed', 1, 'probe', %s, %s, 'test', now(),
            '{"name": "renamed"}')
"""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Renamed(Event):
    TYPE: ClassVar[str] = "probe.thing.renamed"
    VERSION: ClassVar[int] = 1
    name: str


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Sealed(Event):
    TYPE: ClassVar[str] = "vault.thing.sealed"
    VERSION: ClassVar[int] = 1
    name: str


@final
class Modules:
    """``probe`` and ``vault``, their revisions under ``scripts``; what their consumers and job saw."""

    __slots__ = ("exported", "renamed", "scripts", "sealed")

    def __init__(self, scripts: str) -> None:
        self.scripts = scripts
        self.sealed: list[RawEvent] = []
        """What probe's consumer handled."""
        self.renamed: list[RawEvent] = []
        """What vault's consumer handled (in the signer's app only)."""
        self.exported: list[str] = []
        """The keys of vault's jobs it ran."""

    def discover(self) -> Discover:
        found = (
            Discovered("probe", partial(_same, Module(manifest=load_manifest(PROBE), assemble=self._probe))),
            Discovered("vault", partial(_same, Module(manifest=load_manifest(VAULT), assemble=self._vault))),
        )

        def discover(group: str) -> Sequence[Discovered]:
            return found if group == MODULES else ()

        return discover

    def _probe(self, context: ModuleContext) -> ModuleParts:
        del context
        return ModuleParts(
            consumers=(ConsumerSpec(name="probe.sealed", handler=self._on_sealed),),
            migrations=SchemaMigrations(schema="probe", scripts=f"{self.scripts}/probe"),
        )

    def _vault(self, context: ModuleContext) -> ModuleParts:
        own = context.role == "signer"
        return ModuleParts(
            consumers=(ConsumerSpec(name="vault.renamed", handler=self._on_renamed),) if own else (),
            jobs=(JobSpec(kind="vault.export", handler=self._export),) if own else (),
            checks={"ready": _ready},
            migrations=SchemaMigrations(schema="vault", scripts=f"{self.scripts}/vault"),
        )

    async def _on_sealed(self, event: RawEvent, unit: UnitOfWork) -> None:
        del unit
        self.sealed.append(event)

    async def _on_renamed(self, event: RawEvent, unit: UnitOfWork) -> None:
        del unit
        self.renamed.append(event)

    async def _export(self, job: Job) -> JobOutcome:
        self.exported.append(job.key)
        return Done()


def _same(value: object) -> object:
    return value


async def _ready() -> None:
    """Vault's readiness check: every role that assembles it keeps it, its own and the clients'."""


@final
@dataclass(frozen=True, slots=True)
class Fresh:
    """A new database with ``SCHEMAS``, empty; ``vault``'s schema is ``ledger_signer``'s."""

    server: PostgresServer
    name: str

    def url(self, login: str) -> str:
        passwords = {APP: TEST_LOGINS.app, MIGRATOR: TEST_LOGINS.migrator, SIGNER: TEST_LOGINS.signer}
        password = passwords[login]
        assert password is not None
        return self.server.sqlalchemy(self.name, role=login, password=password)

    def rows(self, query: LiteralString) -> list[tuple[object, ...]]:
        with psycopg.connect(self.server.libpq(self.name), autocommit=True) as connection:
            return [tuple(row) for row in connection.execute(query).fetchall()]

    def settings(self, login: str, *, issuer: LocalIssuer | None = None) -> Settings:
        """An app's settings as ``login``, which LISTENs (or migrates) on its own direct URL."""
        return Settings(
            environment="test",
            database_url=SecretStr(self.url(login)),
            database_direct_url=SecretStr(self.url(login)),
            token_issuer=None if issuer is None else issuer.issuer,
            token_audience=None if issuer is None else issuer.audience,
            token_jwks_url=None if issuer is None else issuer.keys_url,
        )

    def migrate(self, modules: Modules) -> None:
        App("migrate", settings=self.settings(MIGRATOR), discover=modules.discover()).migrate()


@pytest.fixture
def modules(tmp_path: Path) -> Modules:
    """``probe`` and ``vault``, each with one revision written to ``tmp_path``."""
    for schema in ("probe", "vault"):
        versions = tmp_path / schema / "versions"
        versions.mkdir(parents=True)
        (tmp_path / schema / "env.py").write_text(ENV)
        (versions / f"r0001_{schema}.py").write_text(REVISION.format(schema=schema))
    return Modules(str(tmp_path))


@pytest.fixture
async def fresh(chassis: WorkerDatabase, postgres: PostgresServer, modules: Modules) -> AsyncGenerator[Fresh]:
    """A fresh database, migrated (the roles come from the template, ``vault``'s the signer login's)."""
    del chassis
    name = fresh_database(postgres, SCHEMAS, isolated=("vault",))
    made = Fresh(postgres, name)
    await asyncio.to_thread(made.migrate, modules)
    yield made
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    """``app`` started (its lifespan), and a client of it."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            yield client


async def test_the_worker_fans_out_an_isolated_schema_it_cannot_read(
    fresh: Fresh, modules: Modules, eventually: Eventually
) -> None:
    worker = App("worker", settings=fresh.settings(APP), discover=modules.discover())
    signer = App("signer", settings=fresh.settings(SIGNER), discover=modules.discover())

    async def sealed() -> bool:
        return [event.type for event in modules.sealed] == ["vault.thing.sealed"]

    try:
        async with serving(worker.asgi()) as client:
            async with signer.bound("vault").unit(TENANT) as unit:  # what the signer's units publish
                unit.publish(Sealed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name="sealed"))
            await eventually(sealed)
            ready = await client.get("/readyz")
            with pytest.raises(DBAPIError) as caught:
                async with worker.bound("vault").unit(TENANT) as unit:
                    await unit.connection.execute(text("SELECT count(*) FROM vault.outbox"))
    finally:
        await signer.aclose()
    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)
    assert fresh.rows("SELECT count(*) FROM vault.outbox") == [(0,)]  # moved by the worker's fan-out
    assert (modules.renamed, modules.exported) == ([], [])  # the worker runs nothing of vault's
    assert ready.status_code == HTTPStatus.OK
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:probe": True,
        "vault:ready": True,
        "listen": True,
        "workers": True,
    }


async def test_the_worker_relays_an_ordinary_module_s_event_into_the_isolated_inbox(
    fresh: Fresh, modules: Modules, eventually: Eventually, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fan-out inserts into ``vault.inbox`` as ``ledger_relay``; the signer's app handles it later."""
    fanned: list[tuple[str, ...]] = []

    def recording(
        database: Database, *, schemas: Sequence[str], signals: Signals, registry: CollectorRegistry
    ) -> Dispatcher:
        fanned.append(tuple(schemas))
        return Dispatcher(database, schemas=schemas, signals=signals, registry=registry)

    monkeypatch.setattr(runtime_app, "Dispatcher", recording)
    worker = App("worker", settings=fresh.settings(APP), discover=modules.discover())

    async def relayed() -> bool:
        query = "SELECT consumer, type, status FROM vault.inbox"
        return fresh.rows(query) == [("vault.renamed", "probe.thing.renamed", "ready")]

    async with serving(worker.asgi()):
        async with worker.bound("probe").unit(TENANT) as unit:
            unit.publish(Renamed(scope=TENANT, subject=uuid.uuid7(), aggregate_version=1, name="renamed"))
        await eventually(relayed)
    assert fanned == [SCHEMAS]  # the fan-out covers the relayed schema with the others
    assert modules.renamed == []  # the row waits for the signer's app: the worker runs nothing of vault's
    assert fresh.rows("SELECT count(*) FROM probe.outbox") == [(0,)]


async def test_the_signer_app_runs_its_consumer_and_its_schedule(
    fresh: Fresh, modules: Modules, eventually: Eventually
) -> None:
    app = App("signer", settings=fresh.settings(SIGNER), discover=modules.discover())
    served = app.asgi()
    before = asyncio.all_tasks()
    with psycopg.connect(
        fresh.server.libpq(fresh.name), autocommit=True
    ) as admin:  # as the fan-out writes it
        admin.execute(INBOX, (uuid.uuid7(), TENANT.organization, TENANT.company))

    async def renamed() -> bool:
        return [event.type for event in modules.renamed] == ["probe.thing.renamed"]

    async def exported() -> bool:
        return any(key.startswith("vault.nightly@") for key in modules.exported)

    async with serving(served) as client:
        await eventually(renamed)
        await eventually(exported)
        ready = await client.get("/readyz")
        live = await client.get("/livez")
        document = await client.get("/openapi.json")
    assert ready.status_code == HTTPStatus.OK
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:vault": True,
        "vault:ready": True,
        "listen": True,
        "workers": True,
    }
    assert live.status_code == HTTPStatus.OK
    assert TypeAdapter(dict[str, object]).validate_python(document.json()["paths"]) == {}
    assert asyncio.all_tasks() - before == set()  # every worker stopped with the app
    assert fresh.rows("SELECT count(*) FROM platform.jobs") == [(0,)]  # no platform job is the signer's
    assert fresh.rows("SELECT status FROM vault.jobs WHERE kind = 'vault.export'") == [("done",)]
    assert modules.sealed == []  # probe is not the signer's


async def test_api_readiness_ignores_an_isolated_schema(
    fresh: Fresh, modules: Modules, local_issuer: LocalIssuer
) -> None:
    """``ledger_app`` cannot read ``vault``'s version table: the api never asks (spec §3.7)."""
    app = App("api", settings=fresh.settings(APP, issuer=local_issuer), discover=modules.discover())
    async with serving(app.asgi()) as client:
        ready = await client.get("/readyz")
    assert ready.status_code == HTTPStatus.OK
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:probe": True,
        "vault:ready": True,
    }
