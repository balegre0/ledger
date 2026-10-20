"""What each app assembles of signer: its gRPC client in the client roles, stand-ins otherwise, its revisions.

A client role (``api``, ``worker``, ``receiver``) given the signer's address and its own token binds one
``SignerClient`` with that token as ``SignerPort`` and ``SignerApi`` and hands it as a resource; without
either it binds the stand-ins answering ``signer_unavailable`` and says so once (Decision 13). No client role
hands a readiness check, a consumer or a job: none depends on the signer. The signer's role binds its facade,
hands its gRPC server, consumer, jobs and checks, and refuses to start without its callers' tokens or its KMS;
``migrate`` runs its revisions.
"""

import dataclasses
import os
import subprocess
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Final
lazy from collections.abc import AsyncGenerator, Callable, Sequence
lazy from pathlib import Path

import grpc
import pytest
from fastapi.routing import APIRoute
from prometheus_client import CollectorRegistry
from pydantic import SecretStr

from ledger_fiscal_kit.ports import (
    SIGNER_PORT,
    SeedToSign,
    SignatureProfile,
    SignerUnavailableError,
    XmlToSign,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.settings import Settings
from ledger_platform.grpc.guard import CallerPolicy, CallerTokenRefusedError, CallerTokens, current_caller
from ledger_platform.grpc.server import ServerOptions, serve_grpc
from ledger_platform.runtime import App, Module, SystemCall
from ledger_platform.runtime.registries import MODULES, Discovered, installed
from ledger_signer.adapters.custody.backends import EntityNotConfiguredError
from ledger_signer.adapters.custody.kms_local import MASTER_KEY_BYTES
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.adapters.grpc.client import SignerClient, SignerTokenRefusedError
from ledger_signer.adapters.grpc.server import SignerNotServingError, SignerServer
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_signer.adapters.serving import KmsKeyFileMissingError
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API, CertificateId
from ledger_signer.application.facade import SignerFacade
from ledger_signer.application.importing import ImportRequest
from ledger_signer.application.ports import Secret
from ledger_signer.assembly import MODULE
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_platform.runtime import AppRole, ModuleContext, ModuleParts
lazy from ledger_platform.runtime.keys import Binding

ROUTES: Final = [
    ("POST", "/v1/certificates"),
    ("POST", "/v1/certificates/remote"),
    ("GET", "/v1/certificates"),
    ("GET", "/v1/certificates/{certificate_id}"),
    ("DELETE", "/v1/certificates/{certificate_id}"),
]
"""What ``api`` mounts of signer, whether it reaches the signer or not."""
URL: Final = "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger"
TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}
TENANT: Final = Tenant(organization=OrganizationId(new_id()), company=CompanyId(new_id()), mode=Mode.TEST)
CALL: Final = SystemCall(reason="test", scope=TENANT)
SETTINGS: Final = Settings(
    environment="test",
    database_url=SecretStr(URL),
    database_direct_url=SecretStr(URL),
    token_issuer="https://issuer.invalid",
    token_audience="ledger",
    token_jwks_url="https://issuer.invalid/keys",
)


class Recorded:
    """What signer's assembly handed each role it assembled in."""

    def __init__(self) -> None:
        self.parts: dict[AppRole, ModuleParts] = {}
        self.contexts: dict[AppRole, ModuleContext] = {}

    def module(self) -> Module:
        """signer's module, its assembly recorded."""

        def assemble(context: ModuleContext) -> ModuleParts:
            parts = MODULE.assemble(context)
            self.parts[context.role] = parts
            self.contexts[context.role] = context
            return parts

        return Module(
            manifest=MODULE.manifest,
            assemble=assemble,
            settings=MODULE.settings,
            migrations=MODULE.migrations,
        )

    def discover(self, group: str) -> Sequence[Discovered]:
        """The installed packages, with identity and the recorded signer as the only modules."""
        found = installed(group)
        if group != MODULES:
            return found
        recorded = self.module()
        return tuple(
            Discovered("signer", lambda: recorded) if entry.name == "signer" else entry
            for entry in found
            if entry.name in {"identity", "signer"}
        )


@pytest.fixture
def recorded() -> Recorded:
    return Recorded()


async def assembled(recorded: Recorded, role: AppRole) -> tuple[App, ModuleParts]:
    """``role``'s app (its pools closed again: nothing connects) and what signer handed it.

    The receiver's app is not built yet (a later milestone): signer is assembled in the worker's context
    given the receiver's role, as the receiver's app will.
    """
    built: AppRole = "worker" if role == "receiver" else role
    app = App(built, settings=SETTINGS, discover=recorded.discover)
    await app.aclose()
    if role == "receiver":
        return app, MODULE.assemble(dataclasses.replace(recorded.contexts[built], role=role))
    return app, recorded.parts[role]


@pytest.fixture
async def signer() -> AsyncGenerator[tuple[str, list[str]]]:
    """A signer on port 0 answering every caller's ``DescribeCertificates`` with nothing: its address, and
    the callers its guard admitted, in order."""
    callers: list[str] = []

    async def describe(
        request: signer_pb2.DescribeCertificatesRequest,
        context: grpc.aio.ServicerContext[
            signer_pb2.DescribeCertificatesRequest, signer_pb2.DescribeCertificatesResponse
        ],
    ) -> signer_pb2.DescribeCertificatesResponse:
        del request, context
        callers.append(current_caller())
        return signer_pb2.DescribeCertificatesResponse()

    def register(server: grpc.aio.Server) -> None:
        handler = grpc.unary_unary_rpc_method_handler(
            describe,
            request_deserializer=signer_pb2.DescribeCertificatesRequest.FromString,
            response_serializer=signer_pb2.DescribeCertificatesResponse.SerializeToString,
        )
        service = codec.DESCRIBE.split("/")[1]
        server.add_generic_rpc_handlers(
            (grpc.method_handlers_generic_handler(service, {"DescribeCertificates": handler}),)
        )

    policies = [
        CallerPolicy(name=name, tokens=(SecretStr(token),), methods=frozenset({codec.DESCRIBE}))
        for name, token in TOKENS.items()
    ]
    async with serve_grpc(
        port=0,
        register=register,
        registry=CollectorRegistry(),
        callers=CallerTokens(policies),
        options=ServerOptions(grace=0.1),
    ) as port:
        yield f"127.0.0.1:{port}", callers


def configure(monkeypatch: pytest.MonkeyPatch, address: str | None, *callers: str) -> None:
    """The signer's address (if any) and the tokens of ``callers``, as a deployment sets them."""
    if address is not None:
        monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", address)
    for caller in callers:
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", TOKENS[caller])


def routes_of(parts: ModuleParts) -> list[tuple[str, str]]:
    """The routes signer hands an app: each method and path."""
    return [
        (method, route.path)
        for router in parts.routers
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    ]


def bound(binding: Binding | None) -> object:
    """What ``binding`` binds; there must be one."""
    assert binding is not None
    return binding.value


def test_the_entry_point_names_the_assembly_the_settings_and_the_revisions() -> None:
    assert (MODULE.manifest.package.name, MODULE.settings, MODULE.migrations) == (
        "signer",
        SignerSettings,
        MIGRATIONS,
    )


@pytest.mark.parametrize("role", ["api", "worker", "receiver"])
async def test_a_configured_client_role_binds_one_client_with_its_own_token(
    recorded: Recorded,
    signer: tuple[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    role: AppRole,
) -> None:
    address, callers = signer
    configure(monkeypatch, address, "api", "worker", "receiver")
    app, parts = await assembled(recorded, role)
    assert parts.api is not None
    assert parts.api.name == SIGNER_API.name
    client = bound(parts.api)
    assert isinstance(client, SignerClient)
    assert [(binding.name, binding.value) for binding in parts.provides] == [(SIGNER_PORT.name, client)]
    assert tuple(parts.resources) == (client,)
    assert (parts.consumers, parts.jobs, dict(parts.checks)) == ((), (), {})  # nothing of the signer's own
    assert [member.relay_only for member in app.members if member.name == "signer"] == [True]
    assert "signer.not_configured" not in capsys.readouterr().out
    assert routes_of(parts) == (ROUTES if role == "api" else [])
    async with AsyncExitStack() as resources:
        for resource in parts.resources:
            _ = await resources.enter_async_context(resource)
        assert await client.certificates(CALL, TENANT.company, Mode.TEST) == ()
    assert callers == [role]


@pytest.mark.parametrize(
    ("address", "callers"),
    [(None, ("api", "worker", "receiver")), ("signer:50051", ()), ("signer:50051", ("api", "receiver"))],
    ids=["no_address", "no_token", "only_others_tokens"],
)
async def test_an_unconfigured_client_role_binds_the_stand_ins_and_says_so_once(
    recorded: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    address: str | None,
    callers: tuple[str, ...],
) -> None:
    configure(monkeypatch, address, *callers)
    _, parts = await assembled(recorded, "worker")
    stand_in = bound(parts.api)
    assert isinstance(stand_in, UnavailableSigner)
    assert [(binding.name, binding.value) for binding in parts.provides] == [(SIGNER_PORT.name, stand_in)]
    assert (parts.consumers, parts.jobs, dict(parts.checks), parts.resources) == ((), (), {}, ())
    assert routes_of(parts) == []
    [line] = [line for line in capsys.readouterr().out.splitlines() if "signer.not_configured" in line]
    assert "warning" in line
    assert "role=worker" in line
    assert all(token not in line for token in TOKENS.values())


@pytest.mark.parametrize("role", ["api", "worker", "receiver"])
async def test_a_client_role_with_a_short_own_token_fails_to_start(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch, role: AppRole
) -> None:
    """A token the signer's guard would refuse is a deployment's defect: found at start, never sent."""
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", "signer:50051")
    monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{role.upper()}", "short-token-0123456789abcdef")
    with pytest.raises(SignerTokenRefusedError):
        await assembled(recorded, role)


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    """A local-file KMS's key, as ``test`` wants it: made beforehand."""
    path = tmp_path / "kms.key"
    path.write_bytes(os.urandom(MASTER_KEY_BYTES))
    return path


def configure_signer(monkeypatch: pytest.MonkeyPatch, key_file: Path) -> None:
    """Every caller's token and the KMS's key file, as the signer's deployment sets them."""
    configure(monkeypatch, None, "api", "worker", "receiver")
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(key_file))


async def test_the_signer_role_binds_its_facade_and_hands_its_service_consumer_jobs_and_checks(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch, key_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_signer(monkeypatch, key_file)
    app, parts = await assembled(recorded, "signer")
    facade = bound(parts.api)
    assert isinstance(facade, SignerFacade)
    assert [(binding.name, binding.value) for binding in parts.provides] == [(SIGNER_PORT.name, facade)]
    [server] = parts.resources
    assert isinstance(server, SignerServer)
    assert [consumer.name for consumer in parts.consumers] == ["signer.company_deactivations"]
    assert [job.kind for job in parts.jobs] == [
        "signer.expiry_notice",
        "signer.purge_idempotency_keys",
        "signer.purge_done_jobs",
    ]
    assert sorted(parts.checks) == ["grpc", "kms"]
    await parts.checks["kms"]()
    with pytest.raises(SignerNotServingError):  # until the app starts it
        await parts.checks["grpc"]()
    assert "signer.not_configured" not in capsys.readouterr().out
    assert [(member.name, member.relay_only) for member in app.members] == [
        ("platform", False),
        ("signer", False),
    ]


def same_token_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_WORKER", TOKENS["api"])


def short_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_RECEIVER", "short-token-0123456789abcdef")


def missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_SIGNER_TOKEN_WORKER")


def previous_token_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_SIGNER_TOKEN_API")
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_API_PREVIOUS", TOKENS["api"])


def no_key_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_SIGNER_KMS_KEY_FILE")


def remote_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", "local,remote")


@pytest.mark.parametrize(
    ("misconfigure", "refusal"),
    [
        (missing_token, CallerTokenRefusedError),
        (short_token, CallerTokenRefusedError),
        (same_token_twice, CallerTokenRefusedError),
        (previous_token_alone, CallerTokenRefusedError),
        (no_key_file, KmsKeyFileMissingError),
        (remote_offered, EntityNotConfiguredError),
    ],
    ids=["missing token", "short token", "shared token", "previous token alone", "no key file", "remote"],
)
async def test_the_signer_refuses_to_start_without_its_callers_tokens_or_its_kms(
    recorded: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    key_file: Path,
    misconfigure: Callable[[pytest.MonkeyPatch], None],
    refusal: type[LedgerError],
) -> None:
    configure_signer(monkeypatch, key_file)
    misconfigure(monkeypatch)
    with pytest.raises(refusal) as caught:
        await assembled(recorded, "signer")
    assert all(token not in str(caught.value) for token in TOKENS.values())


async def test_migrate_hands_the_migrations(recorded: Recorded) -> None:
    app, parts = await assembled(recorded, "migrate")
    assert parts.migrations is not None
    assert parts.migrations.schema == MIGRATIONS.schema == "signer"
    assert [migrations.schema for migrations in app.migrations] == ["platform", "identity", "signer"]


async def test_the_unbound_port_answers_signer_unavailable() -> None:
    stand_in = UnavailableSigner()
    at = datetime(2026, 10, 19, 12, tzinfo=UTC)
    profile = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
    document = XmlToSign(
        tenant=TENANT, profile=profile, document=b"<a/>", signed_at=at, reference="E310000000001"
    )
    seed = SeedToSign(tenant=TENANT, profile=profile, seed=b"<seed/>", audience="ecf.dgii.gov.do")
    with pytest.raises(SignerUnavailableError):
        await stand_in.sign_xml(CALL, document)
    with pytest.raises(SignerUnavailableError):
        await stand_in.sign_seed(CALL, seed)
    with pytest.raises(SignerUnavailableError):
        await stand_in.certificate_validity(CALL, TENANT)
    with pytest.raises(SignerUnavailableError):
        await stand_in.certificates(CALL, TENANT.company, Mode.TEST)
    request = ImportRequest(tenant=TENANT, modes=frozenset({Mode.TEST}), idempotency_key="k")
    with pytest.raises(SignerUnavailableError):
        await stand_in.describe(CALL, TENANT, include_retired=True)
    with pytest.raises(SignerUnavailableError):
        await stand_in.retire(CALL, TENANT, CertificateId(new_id()))
    read: list[bytes] = []
    with pytest.raises(SignerUnavailableError):
        await stand_in.import_local(CALL, request, Secret("password"), unread(read))
    with pytest.raises(SignerUnavailableError):
        await stand_in.import_remote(CALL, request, Secret("credential"))
    assert read == []  # the bundle was left unread


async def unread(read: list[bytes]) -> AsyncGenerator[bytes]:
    """A bundle that records being read in ``read``."""
    read.append(b"bundle")
    yield b"bundle"


@pytest.mark.parametrize("address", [None, "signer:50051"], ids=["unconfigured", "configured"])
async def test_api_mounts_the_certificates_whether_or_not_it_reaches_the_signer(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch, address: str | None
) -> None:
    """Unconfigured, each route answers 503 ``signer_unavailable`` (Decision 13): the document is the same."""
    configure(monkeypatch, address, "api")
    _, parts = await assembled(recorded, "api")
    assert routes_of(parts) == ROUTES


_PROBE: Final = """
import sys
import ledger_signer.assembly as assembly
{touch}
print(" ".join(sorted(name for name in sys.modules if name.startswith("ledger_signer.adapters.custody"))))
"""


def custody_loaded(touch: str) -> list[str]:
    """The custody modules a fresh interpreter holds once it imported the assembly and ran ``touch``."""
    found = subprocess.run(
        [sys.executable, "-c", _PROBE.format(touch=touch)], capture_output=True, text=True, check=True
    )
    return found.stdout.split()


def test_a_client_role_never_loads_custody() -> None:
    """Custody is the signer's own: the api, the worker and the receiver import the assembly without it."""
    assert custody_loaded("assert assembly.MODULE.manifest.package.name == 'signer'") == []


def test_the_probe_sees_custody_once_the_signer_s_role_reaches_for_it() -> None:
    assert "ledger_signer.adapters.custody.kms_local" in custody_loaded("assert assembly.local_kms")
