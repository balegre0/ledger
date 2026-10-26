"""The end-to-end suite (spec §14.2; plan Decisions 27, 28): every installed package in its apps; a fake DGII.

One ``App`` per role on one database that ``ledger-migrate`` brought to head (every installed schema, its
subscriptions and the routing): the signer (the signer module itself, its gRPC service on a free port, a
local-file KMS's key made for the test), then ``api``, ``worker`` and ``receiver``, each reaching it with its
own token, as compose runs them. Every app archives to one file archive (the stack's ``/archive`` volume).
No app reaches the network: the worker's outbound clients (DGII's and the buyers') go over ``Network``, which
hands DGII's hosts (and its emisor-receptor simulator) to the fake DGII and our receiver's public name to the
``receiver`` app; a buyer's host name resolves to a public address (``PUBLIC``), as the DO pack's tests pin.
The fake trusts the certificates this suite issues; the receiver and the worker trust their root (``roots``).

What a test does through the apps it does as an organization's administrator (``owner``), over the ``api``
app's HTTP; it waits for the worker (``eventually``) and reads past row-level security as the superuser
(``admin``). Each test starts on an empty database: what it left is emptied after it, but the subscriptions
and the routing, which ``ledger-migrate`` writes once.
"""

import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from ipaddress import IPv4Address
from typing import Final, LiteralString, final, override
lazy import asyncio
lazy from collections.abc import AsyncGenerator, Callable, Iterator, Sequence
lazy from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.rows import args_row
from pydantic import SecretStr
lazy from fastapi import FastAPI

from ledger_country_do.channels.ecf_v1.adapters.buyers import client as buyers_client
from ledger_country_do.channels.ecf_v1.adapters.http import BUYERS_CLIENT, DGII_CLIENT
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.pki import organization
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_country_do_testing.transports import FAKE_HOSTS
from ledger_fiscal_kit.ports import SIGNER_PORT, SignerPort
from ledger_platform.config.settings import Settings
from ledger_platform.data.roles import APP, MIGRATOR
from ledger_platform.http.outbound import OutboundHttp
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import ChannelPack, Module
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.registries import CHANNELS, MODULES, Discovered, installed
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES
from ledger_signer.adapters.grpc.server import SignerServer
from ledger_testing.api import TestPrincipal
from ledger_testing.database import TEST_LOGINS, app_url, bootstrap, migrator_url, signer_url
from ledger_testing.modules import app_settings, installed_schemas
from ledger_testing.pki import Authority, pem
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_platform.runtime.contexts import ChannelContext, ChannelParts, ModuleContext, ModuleParts
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.pki import Issued
lazy from ledger_testing.postgres import Template, WorkerDatabase

INSTALLED: Final = installed_schemas()
"""Every installed schema, from the manifests: the platform's, each module's, each channel's."""
TOKENS: Final = {
    "api": "e2e-api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "e2e-worker-token-0123456789abcdefghijklmnopq",
    "receiver": "e2e-receiver-token-0123456789abcdefghijklmn",
}
"""Each caller's token of the signer, as deploy/local/signer/tokens.sh writes them (test values)."""
RECEIVER_HOST: Final = "receiver.ledger.test"
RECEIVER_URL: Final = f"https://{RECEIVER_HOST}"
"""Our receiver's public URL (``LEDGER_DO_ECF_RECEIVER_PUBLIC_URL``): ``.test`` never resolves."""
PUBLIC: Final = IPv4Address("93.184.215.14")
"""What every buyer's host name resolves to here (nothing connects to it: ``Network`` answers in process)."""
WORKER_POOL: Final = 78
"""The worker's pool, as compose sets it (``PoolTooSmallError`` names the need)."""
ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role that registers companies (identity's)."""
ISSUER_RNC: Final = "101850043"
BUYER_RNC: Final = "131098193"
PARKED_RNC: Final = "130000012"
"""A company whose certificate is retired before it issues: its document waits for the next one."""
SUPPLIER_RNC: Final = "132000003"
"""Another taxpayer, not ours: it sends one of our companies an e-CF."""
NAMES: Final = {
    ISSUER_RNC: "FERRETERIA DUARTE SRL",
    BUYER_RNC: "CONSTRUCTORA DEL CARIBE SRL",
    PARKED_RNC: "COMERCIAL LA ESPERA SRL",
    SUPPLIER_RNC: "SUMINISTROS DEL ESTE SRL",
}
_KEPT: Final = frozenset({"alembic_version", "routing", "subscriptions"})
"""What emptying the database keeps: Alembic's versions, the routing and each schema's subscriptions."""
_TABLES: Final[LiteralString] = """
    SELECT schemaname::text, tablename::text FROM pg_tables
    WHERE schemaname = ANY(%s) AND NOT tablename = ANY(%s)
"""


def pytest_ledger_template(template: Template) -> None:
    """The roles, then every installed schema as ``ledger-migrate`` makes it (subscriptions, routing too)."""
    bootstrap(template, INSTALLED.schemas, isolated=INSTALLED.isolated)
    settings = Settings(
        environment="test",
        database_url=SecretStr(template.url(role=APP, password=TEST_LOGINS.app)),
        database_direct_url=SecretStr(template.url(role=MIGRATOR, password=TEST_LOGINS.migrator)),
    )
    App("migrate", settings=settings).migrate()


def _emptied(database: WorkerDatabase) -> None:
    """Every table of every installed schema emptied, but what ``_KEPT`` names."""
    with psycopg.connect(database.admin_url(), autocommit=True) as connection:
        cursor = connection.cursor(row_factory=args_row(sql.Identifier))
        tables = cursor.execute(_TABLES, (list(INSTALLED.schemas), list(_KEPT))).fetchall()
        connection.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(tables)))


@pytest.fixture
def database(worker_database: WorkerDatabase) -> Iterator[WorkerDatabase]:
    """This worker's database, migrated; emptied after the test (see the module)."""
    yield worker_database
    _emptied(worker_database)


@pytest.fixture
async def admin(database: WorkerDatabase) -> AsyncGenerator[psycopg.AsyncConnection]:
    """A superuser session, for what a test reads past row-level security."""
    async with await psycopg.AsyncConnection.connect(database.admin_url(), autocommit=True) as connection:
        yield connection


@pytest.fixture(scope="session")
def authority() -> Authority:
    """The suite's certification authority: every company's certificate and the supplier's chain to it."""
    return Authority.root("LEDGER E2E ROOT CA", now=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def certificates(authority: Authority) -> dict[str, Issued]:
    """Each taxpayer's electronic seal (valid since before the samples' dates, for a year from now)."""
    return {
        rnc: authority.issue(organization(rnc, name=name), not_before=SIGNER_VALID_FROM)
        for rnc, name in NAMES.items()
    }


@pytest.fixture(scope="session")
def replacement(authority: Authority) -> Issued:
    """The parked company's second certificate, installed once its first was retired."""
    return authority.issue(organization(PARKED_RNC, name=NAMES[PARKED_RNC]), not_before=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def simulator_root() -> Authority:
    """The root of the fake's emisor-receptor simulator, which the stack's roots file holds too."""
    return Authority.root("LEDGER E2E FAKE DGII CA")


@pytest.fixture
def fake(certificates: dict[str, Issued], replacement: Issued, simulator_root: Authority) -> FakeDgii:
    """The fake DGII, trusting the suite's certificates alone, on DGII's hosts as the suite names them."""
    trusted = [issued.certificate for issued in (*certificates.values(), replacement)]
    simulator = simulator_root.issue(organization("131880738", name="EMISOR RECEPTOR DGII"))
    return FakeDgii(check=dgii_check(trusted=trusted), receiver=simulator, public_url=FAKE_HOSTS.ecf)


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator: it owns its companies."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
def roots(tmp_path: Path, authority: Authority, simulator_root: Authority) -> Path:
    """The trusted roots of do-ecf-v1, as ``deploy/local/do/roots.sh`` writes them: the stack's own and the
    buyer simulator's (here the suite's root, which issued every taxpayer's certificate)."""
    written = tmp_path / "roots.pem"
    written.write_bytes(pem(authority.certificate, simulator_root.certificate))
    return written


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, roots: Path, tmp_path: Path) -> None:
    """``LEDGER_DO_ECF_*`` and ``LEDGER_SIGNER_*`` as compose sets them (``deploy/local/do-ecf.env``), the
    fake's hosts in place of ``http://fake-dgii:8471``; nothing else of the shell's."""
    for name in tuple(os.environ):
        if name.startswith(("LEDGER_DO_ECF_", "LEDGER_SIGNER_")):
            monkeypatch.delenv(name)
    key = tmp_path / "kms.key"
    key.write_bytes(os.urandom(MASTER_KEY_BYTES))
    for caller, token in TOKENS.items():
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", token)
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(key))
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "0")
    monkeypatch.setenv("LEDGER_DO_ECF_ECF_HOST", FAKE_HOSTS.ecf)
    monkeypatch.setenv("LEDGER_DO_ECF_FC_HOST", FAKE_HOSTS.fc)
    monkeypatch.setenv("LEDGER_DO_ECF_RECEIVER_PUBLIC_URL", RECEIVER_URL)
    monkeypatch.setenv("LEDGER_DO_ECF_TRUSTED_ROOTS", str(roots))


async def public_dns(host: str, port: int) -> Sequence[IPv4Address]:
    del host, port
    return (PUBLIC,)


@pytest.fixture(autouse=True)
def public_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every buyer's host name resolves to a public address (``PUBLIC``), as the DO pack's tests pin it."""
    monkeypatch.setattr(buyers_client, "system_resolve", public_dns)


# ── What the apps are given in place of the network and of a fast signer ───────


@final
class Network(httpx.AsyncBaseTransport):
    """The worker's way out (see the module): by the name a request is for, our receiver or the fake."""

    __slots__ = ("_fake", "_receiver")

    def __init__(self, fake: FakeDgii, receiver: FastAPI) -> None:
        self._fake = httpx.ASGITransport(app=fake.app)
        self._receiver = httpx.ASGITransport(app=receiver)

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"].partition(":")[0]
        target = self._receiver if host == RECEIVER_HOST else self._fake
        return await target.handle_async_request(request)


@final
class Gate:
    """Whether the api's signatures wait: while ``held`` is set and not released, they do."""

    __slots__ = ("held",)

    def __init__(self) -> None:
        self.held: asyncio.Event | None = None


@final
class GatedSigner(SignerPort):
    """The api's signer port, the signer module's own, whose document signatures wait while the gate holds."""

    __slots__ = ("_gate", "_inner")

    def __init__(self, inner: SignerPort, gate: Gate) -> None:
        self._inner = inner
        self._gate = gate

    @facade
    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        held = self._gate.held
        if held is not None:
            await held.wait()
        return await self._inner.sign_xml(call, request)

    @facade
    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        return await self._inner.sign_seed(call, request)

    @facade
    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        return await self._inner.certificate_validity(call, tenant)


def _loader(value: object) -> Callable[[], object]:
    def load() -> object:
        return value

    return load


def _over(pack: ChannelPack, transport: httpx.AsyncBaseTransport) -> ChannelPack:
    """``pack``, its outbound clients (DGII's and the buyers') over ``transport``."""

    def assemble(context: ChannelContext) -> ChannelParts:
        transports = {DGII_CLIENT: transport, BUYERS_CLIENT: transport}
        http = OutboundHttp(telemetry=context.telemetry, transports=transports)
        return pack.assemble(replace(context, http=http))

    return replace(pack, assemble=assemble)


def _gated(module: Module, gate: Gate) -> Module:
    """``module``, the signer port it provides behind ``gate``."""

    def assemble(context: ModuleContext) -> ModuleParts:
        parts = module.assemble(context)
        provides = tuple(
            SIGNER_PORT.bind(GatedSigner(SIGNER_PORT.take(binding.value), gate))
            if binding.name == SIGNER_PORT.name
            else binding
            for binding in parts.provides
        )
        return replace(parts, provides=provides)

    return replace(module, assemble=assemble)


def discovering(*, transport: httpx.AsyncBaseTransport | None = None, gate: Gate | None = None) -> Discover:
    """The installed packages, the channels' clients over ``transport``, the signer's port behind ``gate``."""

    def found(group: str) -> Sequence[Discovered]:
        entries = installed(group)
        if group == CHANNELS and transport is not None:
            return tuple(Discovered(entry.name, _loader(_over(_pack(entry), transport))) for entry in entries)
        if group == MODULES and gate is not None:
            return tuple(Discovered(entry.name, _loader(_with_gate(entry, gate))) for entry in entries)
        return entries

    return found


def _pack(entry: Discovered) -> ChannelPack:
    loaded = entry.load()
    assert isinstance(loaded, ChannelPack)
    return loaded


def _with_gate(entry: Discovered, gate: Gate) -> object:
    loaded = entry.load()
    assert isinstance(loaded, Module)
    provides = loaded.manifest.extension_points.provides
    return _gated(loaded, gate) if SIGNER_PORT.name in provides else loaded


# ── The stack ───────────────────────────────────────────────────────────────────


@final
@dataclass(frozen=True, slots=True)
class Stack:
    """The apps, started: a client of the api, our receiver's and the worker's ASGI apps, the fake DGII, the
    api's gate and the apps' file archive."""

    client: httpx.AsyncClient
    receiver: FastAPI
    worker: FastAPI
    fake: FakeDgii
    gate: Gate
    archive: Path

    @asynccontextmanager
    async def receiver_client(self) -> AsyncGenerator[httpx.AsyncClient]:
        """A client of our receiver at its public URL, as another taxpayer reaches it."""
        transport = httpx.ASGITransport(app=self.receiver)
        async with httpx.AsyncClient(transport=transport, base_url=RECEIVER_URL) as opened:
            yield opened

    async def handled(self, consumer: str) -> float:
        """How many events the worker's ``consumer`` handled (``ledger_inbox_handled_total``, ``done``)."""
        transport = httpx.ASGITransport(app=self.worker)
        async with httpx.AsyncClient(transport=transport, base_url="http://worker.test") as probes:
            exported = (await probes.get("/metrics")).text
        wanted = f'ledger_inbox_handled_total{{consumer="{consumer}",outcome="done"}} '
        (line,) = [line for line in exported.splitlines() if line.startswith(wanted)]
        return float(line.removeprefix(wanted))


async def _started(app: App, stack: AsyncExitStack) -> FastAPI:
    served = app.asgi()
    await stack.enter_async_context(served.router.lifespan_context(served))
    return served


def _settings(database: WorkerDatabase, issuer: LocalIssuer, archive: Path, role: AppRole) -> Settings:
    settings = app_settings(
        database_url=app_url(database), direct_url=migrator_url(database), issuer=issuer
    ).model_copy(update={"archive_backend": "file", "archive_path": archive})
    match role:
        case "worker":
            return settings.model_copy(update={"database_pool_size": WORKER_POOL})
        case "receiver":
            return settings.model_copy(update={"receiver_rate_burst": 100_000})
        case _:
            return settings


@pytest.fixture
async def stack(
    database: WorkerDatabase,
    local_issuer: LocalIssuer,
    fake: FakeDgii,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[Stack]:
    """The signer, then ``api``, ``worker`` and ``receiver`` reaching it, all started (see the module)."""
    archive = tmp_path / "archive"
    gate = Gate()
    async with AsyncExitStack() as opened:
        signer_db = signer_url(database)
        signing = App(
            "signer", settings=app_settings(database_url=signer_db, direct_url=signer_db, issuer=local_issuer)
        )
        await _started(signing, opened)
        (server,) = [
            resource
            for member in signing.members
            for resource in member.resources
            if isinstance(resource, SignerServer)
        ]
        monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", f"127.0.0.1:{server.port}")
        receiver = await _started(
            App("receiver", settings=_settings(database, local_issuer, archive, "receiver")), opened
        )
        api = await _started(
            App(
                "api",
                settings=_settings(database, local_issuer, archive, "api"),
                discover=discovering(gate=gate),
            ),
            opened,
        )
        worker = await _started(
            App(
                "worker",
                settings=_settings(database, local_issuer, archive, "worker"),
                discover=discovering(transport=Network(fake, receiver)),
            ),
            opened,
        )
        transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
        client = await opened.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://ledger.test")
        )
        yield Stack(client=client, receiver=receiver, worker=worker, fake=fake, gate=gate, archive=archive)
        if gate.held is not None:
            gate.held.set()
