"""What each app assembles of signer: stand-ins answering ``signer_unavailable`` and its revisions.

Until its client and its server exist every role binds the stand-ins; ``migrate`` runs its revisions; no role
but the signer's runs any of its work.
"""

from datetime import UTC, datetime
from typing import Final
lazy from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from ledger_fiscal_kit.ports import (
    SIGNER_PORT,
    SeedToSign,
    SignatureProfile,
    SignerUnavailableError,
    XmlToSign,
    is_signer_port,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.settings import Settings
from ledger_platform.runtime import App, Module, SystemCall
from ledger_platform.runtime.registries import MODULES, Discovered, installed
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_signer.adapters.settings import SignerSettings
from ledger_signer.adapters.unavailable import UnavailableSigner
from ledger_signer.api import SIGNER_API, is_signer_api
from ledger_signer.assembly import MODULE
lazy from ledger_platform.runtime import AppRole, ModuleContext, ModuleParts

URL: Final = "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger"
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


def test_the_entry_point_names_the_assembly_the_settings_and_the_revisions() -> None:
    assert (MODULE.manifest.package.name, MODULE.settings, MODULE.migrations) == (
        "signer",
        SignerSettings,
        MIGRATIONS,
    )


@pytest.mark.parametrize("role", ["api", "worker"])
async def test_client_roles_bind_the_port_and_the_api(recorded: Recorded, role: AppRole) -> None:
    app, parts = await assembled(recorded, role)
    assert parts.api is not None
    assert parts.api.name == SIGNER_API.name
    assert is_signer_api(parts.api.value)
    assert [(binding.name, is_signer_port(binding.value)) for binding in parts.provides] == [
        (SIGNER_PORT.name, True)
    ]
    assert (parts.consumers, parts.jobs, dict(parts.checks), parts.resources) == ((), (), {}, ())
    assert [member.relay_only for member in app.members if member.name == "signer"] == [True]


async def test_the_signer_role_binds_the_stand_ins_until_its_service_exists(recorded: Recorded) -> None:
    app, parts = await assembled(recorded, "signer")
    assert parts.api is not None
    assert is_signer_api(parts.api.value)
    assert [binding.name for binding in parts.provides] == [SIGNER_PORT.name]
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
