"""Fixtures of the DO pack's tests: one test hierarchy and one signer per session (RSA keys are slow).

The channel's own suites (its schema, its assembly, its routes) run it as the apps do: the worker's database
migrated as production migrates it, the ``api`` app serving identity, fiscal and the signer (whose port is the
test's ``TestSigner``) beside the channel (``ledger_testing.modules``).
"""

import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Final, Protocol, final, override
from uuid import UUID
lazy from collections.abc import AsyncIterator, Iterator, Mapping
lazy from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter
lazy import psycopg

from ledger_country_do._manifest import MANIFEST
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.postgres.schema import MIGRATIONS
from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
from ledger_country_do.channels.ecf_v1.assembly import CHANNEL
from ledger_country_do.regime import REGIME
from ledger_country_do_testing.fake_dgii.app import FakeDgii
from ledger_country_do_testing.fake_dgii.state import dgii_check
from ledger_country_do_testing.local_signer import KeyPairSigner
from ledger_country_do_testing.pki import organization, person
from ledger_country_do_testing.samples import SIGNER_VALID_FROM
from ledger_country_do_testing.transports import FAKE_HOSTS, outbound_over
from ledger_fiscal_kit.ports import ARCHIVE_PORT, COMPANY_DIRECTORY, SIGNER_PORT, CompanyDirectory
from ledger_kernel.access import CompanyNotFoundError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.settings import Settings
from ledger_platform.data.audit import Actor, acting_as
from ledger_platform.http.authentication import Authentication
from ledger_platform.http.outbound import OutboundHttp
from ledger_platform.observability.telemetry import Telemetry
from ledger_platform.runtime.app import App
from ledger_platform.runtime.contexts import ChannelContext
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.keys import Binding, Directory
from ledger_testing.api import TestPrincipal, created
from ledger_testing.clock import ManualClock
from ledger_testing.modules import ModuleUnderTest
from ledger_testing.pki import Authority, pem
from ledger_testing.signer import TestSigner, with_test_signer
lazy from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings
lazy from ledger_fiscal_kit.ports import ReceivingCompany
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_platform.config.manifest import AppRole
lazy from ledger_platform.config.settings import Environment
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.archiving import StoreArchive
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.pki import Issued
lazy from ledger_testing.postgres import Template

SERVING: Final = ("fiscal", "signer")
"""The modules whose ports the channel reads: fiscal's ``CompanyDirectory``, the signer's ``SignerPort``."""
TEST_ACTOR: Final = Actor("system:test", "do-ecf-tests")
"""Who what a test does itself acts as; a request through the app acts as its caller instead."""
RECEIVER_URL: Final = "https://receiver.ledger.test"
UNAUTHENTICATED: Final = Settings(
    environment="test", database_url=SecretStr("postgresql+psycopg://x@db.invalid/x")
)
"""No token settings: the hand-made context's authentication trusts no one (no route is called through it)."""


@pytest.fixture(scope="session")
def authority() -> Authority:
    return Authority.root(now=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def intermediate(authority: Authority) -> Authority:
    return authority.intermediate()


@pytest.fixture(scope="session")
def issued(intermediate: Authority) -> Issued:
    return intermediate.issue(person(), not_before=SIGNER_VALID_FROM)


@pytest.fixture(scope="session")
def signer(issued: Issued) -> KeyPairSigner:
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 10, 13, 13, 30, tzinfo=UTC))


@pytest.fixture
def fake(authority: Authority, issued: Issued, clock: ManualClock) -> FakeDgii:
    receiver = authority.issue(
        organization("131098193", name="CONSTRUCTORA DEL CARIBE SRL"), not_before=SIGNER_VALID_FROM
    )
    return FakeDgii(
        check=dgii_check(trusted=[issued.certificate]),
        receiver=receiver,
        clock=clock,
        public_url=FAKE_HOSTS.ecf,
    )


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def dgii(
    signer: KeyPairSigner, fake: FakeDgii, clock: ManualClock, sleeps: list[float]
) -> AsyncIterator[DgiiClient]:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = DgiiClient(
        DocumentSigner(signer),
        hosts=FAKE_HOSTS,
        clock=clock,
        sleep=sleep,
        http=outbound_over(httpx.ASGITransport(app=fake.app)),
    )
    async with client.opened():
        yield client


# ── The channel in its apps: PostgreSQL, the modules that serve its ports ───────


UNDER_TEST: Final = ModuleUnderTest(MANIFEST, MIGRATIONS, serving=SERVING)
"""The channel's schema, served with the modules that provide its ports: fiscal's company directory (and
identity, which fiscal depends on) and the signer, whose port is each test's ``TestSigner``."""
ORGANIZATION_ADMIN: Final = "ledger.organization.admin"
"""The identity provider's project role that registers companies (identity's)."""
RNC: Final = "101850043"
COMPANY: Final = {
    "jurisdiction": {"country": "DO"},
    "functional_currency": "DOP",
    "tax_ids": [{"scheme": "do.rnc", "value": RNC}],
    "legal_name": "Ferreteria Duarte SRL",
}
"""What the ``company`` fixture registers through identity's API."""


def pytest_ledger_template(template: Template) -> None:
    """The roles, and the schemas of the platform, identity, fiscal, the signer and do_ecf, each at head."""
    UNDER_TEST.prepare(template)


@pytest.fixture
def module_under_test() -> ModuleUnderTest:
    """do-ecf-v1's schema, for the module fixtures of ``ledger_testing``."""
    return UNDER_TEST


@pytest.fixture(autouse=True)
def acting() -> Iterator[None]:
    """Every test acts as ``TEST_ACTOR``: what its own units audit needs an actor."""
    with acting_as(TEST_ACTOR):
        yield


@pytest.fixture
def channel_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LEDGER_DO_ECF_*`` as a deployment that registered its receiver sets it; nothing else of the shell."""
    for name in tuple(os.environ):
        if name.startswith("LEDGER_DO_ECF_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LEDGER_DO_ECF_RECEIVER_PUBLIC_URL", RECEIVER_URL)


@pytest.fixture
def archived(settings: Settings, tmp_path: Path) -> Settings:
    """The apps' settings with a file archive of the test's own."""
    return settings.model_copy(update={"archive_backend": "file", "archive_path": tmp_path / "archive"})


@pytest.fixture
def api(
    archived: Settings, module_under_test: ModuleUnderTest, test_signer: TestSigner, channel_environment: None
) -> App:
    """The ``api`` app: identity, fiscal and the signer (its port the test's), and do-ecf-v1."""
    del channel_environment
    return App(
        "api", settings=archived, discover=with_test_signer(module_under_test.discovering(), test_signer)
    )


@pytest.fixture
def owner(local_issuer: LocalIssuer) -> TestPrincipal:
    """Subject ``2001`` of the provider's organization ``1001``, its administrator: it owns its companies."""
    return TestPrincipal(local_issuer, roles=(ORGANIZATION_ADMIN,))


@pytest.fixture
async def company(client: httpx.AsyncClient, owner: TestPrincipal) -> str:
    """The public id of a company ``owner`` registered through identity's API (``COMPANY``)."""
    return await created(client, "/v1/companies", COMPANY, owner.posting("company-1"))


# ── The receiver: emitters (and DGII, in certification) send the companies documents ──


_UUID: Final = TypeAdapter(UUID)
_TOKEN: Final = TypeAdapter(dict[str, str])
RECEIVING_RNC: Final = "131098193"
"""The receiving company's RNC: the sample e-CF's buyer (``CONSTRUCTORA DEL CARIBE SRL``)."""
SENDER_RNC: Final = "101850043"
"""The sample e-CF's issuer (``FERRETERIA DUARTE SRL``): the emitter that sends it."""
RECEIVER_PATHS: Final = "/do-ecf-v1/{environment}/{rnc}/fe"
SEED_PATH: Final = "autenticacion/api/semilla"
VALIDATION_PATH: Final = "autenticacion/api/validacioncertificado"


class Enroll(Protocol):
    """``enroll``: a company of ``rnc`` (registered once) enrolled on do-ecf-v1 in ``environment``."""

    async def __call__(self, rnc: str, environment: str = "testecf") -> Tenant: ...


class Authenticate(Protocol):
    """``authenticate``: a seed of the company ``rnc`` in ``environment``, signed by ``signer``, validated."""

    async def __call__(
        self, signer: KeyPairSigner, *, rnc: str = RECEIVING_RNC, environment: str = "testecf"
    ) -> httpx.Response: ...


def _receiver_path(path: str, *, rnc: str = RECEIVING_RNC, environment: str = "testecf") -> str:
    """A receiver route of the company ``rnc`` in ``environment``: ``path`` under its base."""
    return f"{RECEIVER_PATHS.format(environment=environment, rnc=rnc)}/{path}"


@pytest.fixture(scope="session")
def sender(authority: Authority) -> KeyPairSigner:
    """The emitter the sample e-CF names (``SENDER_RNC``), its certificate issued under the trusted root."""
    issued = authority.issue(organization(SENDER_RNC), not_before=SIGNER_VALID_FROM)
    return KeyPairSigner.from_issued(issued)


@pytest.fixture
def roots(tmp_path: Path, authority: Authority) -> Path:
    """The receiver's trusted roots: the tests' root, which issued every emitter's certificate."""
    written = tmp_path / "roots.pem"
    written.write_bytes(pem(authority.certificate))
    return written


@pytest.fixture
def standins() -> str:
    """``LEDGER_DO_ECF_TEST_SENDER_STANDINS`` of the receiver: none (a test parametrizes it)."""
    return ""


@pytest.fixture
def receiver_environment(
    channel_environment: None, roots: Path, standins: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LEDGER_DO_ECF_*`` of a receiver: the channel's, its trusted roots and its stand-ins."""
    del channel_environment
    monkeypatch.setenv("LEDGER_DO_ECF_TRUSTED_ROOTS", str(roots))
    monkeypatch.setenv("LEDGER_DO_ECF_TEST_SENDER_STANDINS", standins)


@pytest.fixture
def receiver(
    archived: Settings,
    module_under_test: ModuleUnderTest,
    signer: KeyPairSigner,
    receiver_environment: None,
    clock: ManualClock,
) -> App:
    """The ``receiver`` app on the test's clock: its receipts signed by ``signer`` (the receiving company's).

    Its per-client limit is out of the tests' way (one client asks everything); the per-RNC one is not.
    """
    del receiver_environment
    settings = archived.model_copy(update={"receiver_rate_burst": 100_000})
    port = TestSigner(sign=signer.sign, sign_seed=signer.sign)
    discover = with_test_signer(module_under_test.discovering(), port)
    return App("receiver", settings=settings, discover=discover, clock=clock)


@pytest.fixture
async def receiver_client(receiver: App, client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """A client of the ``receiver`` app, started after the ``api`` app (whose client sets companies up)."""
    del client
    served = receiver.asgi()
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=RECEIVER_URL) as opened:
            yield opened


@pytest.fixture
def enroll(client: httpx.AsyncClient, owner: TestPrincipal, admin: psycopg.AsyncConnection) -> Enroll:
    """Companies of the owner's organization enrolled on do-ecf-v1, as identity and fiscal record them."""
    registered: dict[str, str] = {}

    async def enrolled(rnc: str, environment: str = "testecf") -> Tenant:
        mode = "live" if environment == "ecf" else "test"
        if rnc not in registered:
            registration = {
                "jurisdiction": {"country": "DO"},
                "functional_currency": "DOP",
                "tax_ids": [{"scheme": "do.rnc", "value": rnc}],
                "legal_name": f"Receptora {rnc} SRL",
            }
            registered[rnc] = await created(
                client, "/v1/companies", registration, owner.posting(f"company-{rnc}")
            )
        headers = owner.posting(f"enroll-{rnc}-{environment}", mode) | {"Ledger-Company": registered[rnc]}
        body = {"channel": "do-ecf-v1", "environment": environment}
        response = await client.post("/v1/fiscal/enrollments", json=body, headers=headers)
        assert response.status_code == HTTPStatus.CREATED, response.text
        found = await admin.execute(
            "SELECT organization_id, company_id FROM identity.company_tax_ids WHERE value = %s", (rnc,)
        )
        row = await found.fetchone()
        assert row is not None
        organization, company = _UUID.validate_python(row[0]), _UUID.validate_python(row[1])
        return Tenant(OrganizationId(organization), CompanyId(company), Mode(mode))

    return enrolled


@pytest.fixture
async def receiving(enroll: Enroll) -> Tenant:
    """The company ``RECEIVING_RNC``, enrolled on do-ecf-v1 in ``testecf`` (test mode)."""
    return await enroll(RECEIVING_RNC)


@pytest.fixture
def authenticate(receiver_client: httpx.AsyncClient) -> Authenticate:
    """DGII's authentication with a receiver: a seed asked for, signed by the sender, validated."""

    async def validated(
        signer: KeyPairSigner, *, rnc: str = RECEIVING_RNC, environment: str = "testecf"
    ) -> httpx.Response:
        seed = await receiver_client.get(_receiver_path(SEED_PATH, rnc=rnc, environment=environment))
        assert seed.status_code == HTTPStatus.OK, seed.text
        files = {"xml": ("semilla.xml", signer.sign(seed.content), "text/xml")}
        return await receiver_client.post(
            _receiver_path(VALIDATION_PATH, rnc=rnc, environment=environment), files=files
        )

    return validated


@pytest.fixture
async def token(receiving: Tenant, authenticate: Authenticate, sender: KeyPairSigner) -> str:
    """A token of ``sender`` (``SENDER_RNC``) for the receiving company in ``testecf``."""
    del receiving
    validated = await authenticate(sender)
    assert validated.status_code == HTTPStatus.OK, validated.text
    return _TOKEN.validate_json(validated.content)["token"]


# ── The channel assembled by hand: its context, as the app makes it ─────────────


@final
class NoCompanies(CompanyDirectory):
    """A company directory that knows no company: for what reads the port without asking it."""

    @facade
    @override
    async def receiving_company(
        self, call: SystemCall, *, tax_id: TaxId, channel: str, environment: str
    ) -> ReceivingCompany | None:
        del call, tax_id, channel, environment
        return None

    @facade
    @override
    async def company(self, call: SystemCall, company: CompanyId) -> ReceivingCompany:
        del call
        raise CompanyNotFoundError(company)

    @facade
    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        del principal, company
        return frozenset()


@pytest.fixture
def ports(test_signer: TestSigner, archive: StoreArchive) -> dict[str, object]:
    """The channel's ports as the app binds them: the test's signer, no company, the test's archive."""
    return {SIGNER_PORT.name: test_signer, COMPANY_DIRECTORY.name: NoCompanies(), ARCHIVE_PORT.name: archive}


class Contexts(Protocol):
    """A channel context as the app makes it (``contexts``); a test that uses one declares its own."""

    def __call__(
        self,
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: Environment = "test",
        http: OutboundHttp | None = None,
    ) -> ChannelContext: ...


@pytest.fixture
def contexts(module_database: BoundDatabase, clock: ManualClock, ports: dict[str, object]) -> Contexts:
    """Contexts as the app hands the channel.

    ``ports`` by default (each one declared; a test may leave one unbound), the channel's own units, the
    test's clock, an authentication that trusts no one (no route is called through it) and clients to call
    out with (``http``: over the network unless a test gives its own, the fake DGII's).
    """

    def context(
        *,
        settings: DoEcfSettings,
        bound: Mapping[str, object] | None = None,
        role: AppRole = "api",
        environment: Environment = "test",
        http: OutboundHttp | None = None,
    ) -> ChannelContext:
        directory = Directory("port")
        for name, value in (ports if bound is None else bound).items():
            directory.add(Binding(name, value), by="tests", declared=frozenset({name}))
        telemetry = Telemetry.unexported(CHANNEL.key)
        return ChannelContext(
            definition=CHANNEL.entry,
            regime=REGIME,
            role=role,
            environment=environment,
            settings=settings,
            database=module_database,
            clock=clock,
            ports=directory.view(frozenset({SIGNER_PORT.name, COMPANY_DIRECTORY.name, ARCHIVE_PORT.name})),
            telemetry=telemetry,
            auth=Authentication.of(UNAUTHENTICATED),
            http=OutboundHttp(telemetry=telemetry) if http is None else http,
        )

    return context
