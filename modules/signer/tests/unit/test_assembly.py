"""What each app assembles of signer: its gRPC client in the client roles, stand-ins otherwise, its revisions.

A client role (``api``, ``worker``, ``receiver``) given the signer's address and its own token binds one
``SignerClient`` with that token as ``SignerPort`` and ``SignerApi`` and hands it as a resource; without
either it binds the stand-ins answering ``signer_unavailable`` and says so once (Decision 13). No client role
hands a readiness check: none depends on the signer. The signer's role binds the stand-ins until its service
exists; ``migrate`` runs its revisions.
"""

from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Final
lazy from collections.abc import AsyncGenerator, Sequence

import grpc
import pytest
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
from ledger_platform.grpc.guard import CallerPolicy, CallerTokens, current_caller
from ledger_platform.grpc.server import ServerOptions, serve_grpc
from ledger_platform.runtime import App, Module, SystemCall
from ledger_platform.runtime.registries import MODULES, Discovered, installed
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.adapters.grpc.client import SignerClient
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API
from ledger_signer.assembly import MODULE
lazy from ledger_platform.runtime import AppRole, ModuleContext, ModuleParts
lazy from ledger_platform.runtime.keys import Binding

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

    def module(self) -> Module:
        """signer's module, its assembly recorded."""

        def assemble(context: ModuleContext) -> ModuleParts:
            parts = MODULE.assemble(context)
            self.parts[context.role] = parts
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
    """``role``'s app (its pools closed again: nothing connects) and what signer handed it."""
    app = App(role, settings=SETTINGS, discover=recorded.discover)
    await app.aclose()
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


@pytest.mark.parametrize("role", ["api", "worker"])
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
    assert (parts.consumers, parts.jobs, dict(parts.checks)) == ((), (), {})  # no readiness on the signer
    assert [member.relay_only for member in app.members if member.name == "signer"] == [True]
    assert "signer.not_configured" not in capsys.readouterr().out
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
    [line] = [line for line in capsys.readouterr().out.splitlines() if "signer.not_configured" in line]
    assert "warning" in line
    assert "role=worker" in line
    assert all(token not in line for token in TOKENS.values())


async def test_the_signer_role_binds_the_stand_ins_until_its_service_exists(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch, "signer:50051", "api", "worker", "receiver")
    app, parts = await assembled(recorded, "signer")
    assert isinstance(bound(parts.api), UnavailableSigner)
    assert [binding.name for binding in parts.provides] == [SIGNER_PORT.name]
    assert parts.resources == ()
    assert "signer.not_configured" not in capsys.readouterr().out
    assert [(member.name, member.relay_only) for member in app.members] == [
        ("platform", False),
        ("signer", False),
    ]


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
