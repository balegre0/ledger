"""The ``receiver`` role on PostgreSQL (§3.3, §3.8, §10; Review Focus 3).

Every test gets a database of its own with the schemas ``platform`` and ``probe``, migrated by the ``migrate``
app: ``probe`` is the schema of the channel ``us-probe-v1``, whose receiver routes take an inbound document
(2 MiB at most, as an e-CF reception does) and a seed. The receiver is made without token settings: nothing
of it proves our tokens.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from http import HTTPStatus
from typing import Final, final
lazy from collections.abc import AsyncGenerator, Mapping, Sequence
lazy from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import APIRouter, Depends
from psycopg import sql
from pydantic import SecretStr, TypeAdapter
lazy from fastapi import FastAPI, Request

from ledger_kernel.dated import Since, Timeline
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source
from ledger_platform.config.manifest import ChannelEntry
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.data.roles import APP, MIGRATOR
from ledger_platform.http.limits import larger_body
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts
from ledger_platform.runtime.registries import CHANNELS, REGIMES, Discovered
from ledger_testing.database import TEST_LOGINS, fresh_database
lazy from ledger_platform.runtime.contexts import ChannelContext
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_testing.postgres import PostgresServer, WorkerDatabase

CHECKS: Final = TypeAdapter(dict[str, bool])
SCHEMAS: Final = ("platform", "probe")
INBOUND_LIMIT: Final = 2 * 1024 * 1024
"""What one inbound document may weigh (an e-CF reception, §10)."""
FIRST: Final = "203.0.113.7"
OTHER: Final = "198.51.100.1"
RECEPTION: Final = "/us-probe-v1/testecf/131880738/fe/recepcion/api/ecf"
ENV: Final = "from ledger_platform.data.migrations import run_schema_migrations\n\nrun_schema_migrations()\n"
REVISION: Final = """from alembic import op

from ledger_platform.data.chassis_schema import chassis_schema_v1, chassis_schema_v2

revision = "r0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    chassis_schema_v1(op, "probe")
    chassis_schema_v2(op, "probe")
"""
ENTRY: Final = ChannelEntry.model_validate(
    {
        "key": "us-probe-v1",
        "entry_point": "ledger_country_us.channels.probe:PACK",
        "schema": "probe",
        "valid_from": date(2026, 1, 1),
        "environments": ["test"],
        "document_kinds": ["invoice"],
        "directions": ["sales"],
        "tier": "bronze",
    }
)


def us_regime() -> RegimeDef:
    return RegimeDef(
        key=RegimeKey("us"),
        jurisdiction=Jurisdiction(CountryCode("US"), None),
        currency=Currency.of("USD"),
        time_zone="America/New_York",
        labels={Locale("en"): "Probe"},
        tax_id_schemes=(),
        tax_categories=(),
        document_types=(),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP,
            points=frozenset({RoundingPoint.LINE}),
            places=2,
            line_tolerance=Decimal("0.01"),
        ),
        fields=(),
        parameters={"threshold": Timeline(Since(date(2020, 1, 1), Decimal(1000)))},
        sources=(
            Source(
                title="Probe law", url="https://law.invalid", version="1", retrieved_on=date(2026, 10, 13)
            ),
        ),
    )


@final
class Probe:
    """The channel ``us-probe-v1``: its revisions under ``scripts``, and the sizes of what it received."""

    __slots__ = ("received", "scripts")

    def __init__(self, scripts: str) -> None:
        self.scripts = scripts
        self.received: list[int] = []

    def discover(self) -> Discover:
        groups: Mapping[str, tuple[Discovered, ...]] = {
            REGIMES: (Discovered("us", us_regime),),
            CHANNELS: (Discovered("us-probe-v1", partial(_same, self._pack())),),
        }

        def discover(group: str) -> Sequence[Discovered]:
            return groups.get(group, ())

        return discover

    def _pack(self) -> ChannelPack:
        return ChannelPack(entry=ENTRY, regime="us", assemble=self._assemble)

    def _assemble(self, context: ChannelContext) -> ChannelParts:
        router = APIRouter(prefix="/us-probe-v1/{environment}/{rnc}")
        received = self.received

        @router.get("/fe/autenticacion/api/semilla")
        async def seed(environment: str, rnc: str) -> dict[str, str]:
            return {"environment": environment, "rnc": rnc}

        @router.post("/fe/recepcion/api/ecf", dependencies=[Depends(larger_body(INBOUND_LIMIT))])
        async def reception(request: Request) -> int:
            body = await request.body()
            received.append(len(body))
            return len(body)

        return ChannelParts(
            channel="probe",
            receiver_routers=(router,),
            migrations=SchemaMigrations(schema=context.database.schema, scripts=f"{self.scripts}/probe"),
        )


def _same(value: object) -> object:
    return value


@final
@dataclass(frozen=True, slots=True)
class Fresh:
    """A new database with ``SCHEMAS``, migrated."""

    server: PostgresServer
    name: str

    def settings(self, login: str, **changes: float) -> Settings:
        password = {APP: TEST_LOGINS.app, MIGRATOR: TEST_LOGINS.migrator}[login]
        assert password is not None
        url = SecretStr(self.server.sqlalchemy(self.name, role=login, password=password))
        return Settings(environment="test", database_url=url, database_direct_url=url).model_copy(
            update=changes
        )


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    versions = tmp_path / "probe" / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "probe" / "env.py").write_text(ENV)
    (versions / "r0001_probe.py").write_text(REVISION)
    return Probe(str(tmp_path))


@pytest.fixture
async def fresh(chassis: WorkerDatabase, postgres: PostgresServer, probe: Probe) -> AsyncGenerator[Fresh]:
    del chassis
    made = Fresh(postgres, fresh_database(postgres, SCHEMAS, isolated=()))
    migrator = App("migrate", settings=made.settings(MIGRATOR), discover=probe.discover())
    await asyncio.to_thread(migrator.migrate)
    yield made
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(made.name)))


@asynccontextmanager
async def serving(app: FastAPI, *addresses: str) -> AsyncGenerator[tuple[httpx.AsyncClient, ...]]:
    """``app`` started (its lifespan), and a client of it at each of ``addresses``."""
    async with app.router.lifespan_context(app), AsyncExitStack() as clients:
        yield tuple(
            [
                await clients.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app, client=(address, 4711)),
                        base_url="http://receiver.test",
                    )
                )
                for address in addresses
            ]
        )


async def test_the_receiver_is_ready_with_its_database_and_its_channels_schemas(
    fresh: Fresh, probe: Probe
) -> None:
    """No listener, no workers and no signer among its checks (§11): a signer that is down is not its."""
    app = App("receiver", settings=fresh.settings(APP), discover=probe.discover())
    async with serving(app.asgi(), FIRST) as (client,):
        ready = await client.get("/readyz")
        live = await client.get("/livez")
        seed = await client.get("/US-PROBE-V1/TestEcf/131880738/FE/Autenticacion/API/Semilla")
    assert (ready.status_code, live.status_code) == (HTTPStatus.OK, HTTPStatus.OK)
    assert CHECKS.validate_python(ready.json()["checks"]) == {
        "database": True,
        "schema:platform": True,
        "schema:probe": True,
    }
    assert seed.json() == {"environment": "testecf", "rnc": "131880738"}


async def test_a_client_over_its_rate_is_answered_429_and_the_probes_still_answer(
    fresh: Fresh, probe: Probe
) -> None:
    settings = fresh.settings(APP, receiver_rate_per_second=1.0, receiver_rate_burst=2)
    app = App("receiver", settings=settings, discover=probe.discover())
    async with serving(app.asgi(), FIRST, OTHER) as (client, other):
        answered = [(await client.post(RECEPTION, content=b"<ECF/>")).status_code for _ in range(3)]
        retry = (await client.post(RECEPTION, content=b"<ECF/>")).headers["retry-after"]
        ready = await client.get("/readyz")
        another = await other.post(RECEPTION, content=b"<ECF/>")
    assert answered == [HTTPStatus.OK, HTTPStatus.OK, HTTPStatus.TOO_MANY_REQUESTS]
    assert (retry, ready.status_code, another.status_code) == ("1", HTTPStatus.OK, HTTPStatus.OK)
    assert probe.received == [6, 6, 6]  # two from the first client, one from the other


async def test_an_inbound_body_over_its_route_s_limit_is_refused_before_it_is_read(
    fresh: Fresh, probe: Probe
) -> None:
    app = App("receiver", settings=fresh.settings(APP), discover=probe.discover())
    async with serving(app.asgi(), FIRST) as (client,):
        taken = await client.post(RECEPTION, content=b"x" * INBOUND_LIMIT)
        refused = await client.post(RECEPTION, content=b"x" * (3 * 1024 * 1024))
        unknown = await client.get("/v1/invoices")
    assert (taken.status_code, refused.status_code) == (HTTPStatus.OK, HTTPStatus.CONTENT_TOO_LARGE)
    assert refused.json()["code"] == "payload_too_large"
    assert probe.received == [INBOUND_LIMIT]  # the refused body never reached the route
    assert unknown.status_code == HTTPStatus.NOT_FOUND
