"""``TestSigner``: ``SignerPort`` for tests, signing with what the test gives it, failing when told to."""

from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from typing import Final
from uuid import UUID
lazy from collections.abc import Mapping, Sequence

import pytest
from pydantic import SecretStr

from ledger_fiscal_kit.ports import (
    SIGNER_PORT,
    CertificateValidity,
    SeedToSign,
    SignatureProfile,
    SignatureRefusedError,
    Signed,
    SignerPort,
    SignerUnavailableError,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.config.manifest import ChannelEntry, load_manifest
from ledger_platform.config.settings import Settings
from ledger_platform.data.migrations import SchemaMigrations
from ledger_platform.runtime.app import App
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts, Module, ModuleParts
from ledger_platform.runtime.facade import facade, require_facades
from ledger_platform.runtime.registries import CHANNELS, MODULES, REGIMES, Discovered
from ledger_testing.signer import (
    DEFAULT_VALIDITY,
    SignCall,
    SignerProviderMissingError,
    TestSigner,
    with_test_signer,
)
lazy from ledger_platform.runtime.contexts import ChannelContext, ModuleContext
lazy from ledger_platform.runtime.registries import Discover

TENANT: Final = Tenant(OrganizationId(UUID(int=1)), CompanyId(UUID(int=2)), Mode.TEST)
CALL: Final = SystemCall(reason="probe", scope=TENANT)
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
SIGNED_AT: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
VALIDITY: Final = CertificateValidity(
    certificate=UUID(int=9),
    not_before=datetime(2026, 1, 1, tzinfo=UTC),
    not_after=datetime(2027, 1, 1, tzinfo=UTC),
)
URL: Final = "postgresql+psycopg://ledger_app:app@db.invalid:5432/ledger"


def document(reference: str = "E310000000001", signed_at: datetime = SIGNED_AT) -> XmlToSign:
    return XmlToSign(
        tenant=TENANT, profile=PROFILE, document=b"<ECF/>", signed_at=signed_at, reference=reference
    )


def seed() -> SeedToSign:
    return SeedToSign(tenant=TENANT, profile=PROFILE, seed=b"<SemillaModel/>", audience="ecf.dgii.gov.do")


def signed(content: bytes) -> bytes:
    return content + b"<Signature/>"


async def test_a_document_is_signed_by_the_function_given_and_the_call_recorded() -> None:
    signer = TestSigner(sign=signed, validity=VALIDITY)
    assert await signer.sign_xml(CALL, document()) == Signed(
        document=b"<ECF/><Signature/>", certificate=VALIDITY.certificate
    )
    assert signer.calls == [SignCall(tenant=TENANT, profile=PROFILE, reference="E310000000001")]
    require_facades(signer)
    assert isinstance(signer, SignerPort)


def at_signing() -> datetime:
    return SIGNED_AT


def after_expiry() -> datetime:
    return datetime(2027, 2, 1, tzinfo=UTC)


async def test_a_seed_is_signed_by_its_own_function_and_its_audience_recorded() -> None:
    signer = TestSigner(sign_seed=signed, validity=VALIDITY, clock=at_signing)
    assert (await signer.sign_seed(CALL, seed())).document == b"<SemillaModel/><Signature/>"
    assert signer.calls == [SignCall(tenant=TENANT, profile=PROFILE, audience="ecf.dgii.gov.do")]


async def test_by_default_it_signs_nothing_and_its_certificate_serves_any_moment() -> None:
    signer = TestSigner()
    assert (await signer.sign_xml(CALL, document())).document == b"<ECF/>"
    assert (await signer.sign_seed(CALL, seed())).document == b"<SemillaModel/>"
    assert await signer.certificate_validity(CALL, TENANT) == DEFAULT_VALIDITY


async def test_it_fails_with_the_errors_it_is_given_one_call_each_in_order() -> None:
    refused = SignatureRefusedError("xml_document_signed")
    signer = TestSigner(failures=[SignerUnavailableError(), refused, SigningClockSkewError()])
    with pytest.raises(SignerUnavailableError):
        await signer.sign_xml(CALL, document("E310000000001"))
    with pytest.raises(SignatureRefusedError) as caught:
        await signer.certificate_validity(CALL, TENANT)
    assert caught.value is refused
    with pytest.raises(SigningClockSkewError):
        await signer.sign_seed(CALL, seed())
    assert (await signer.sign_xml(CALL, document("E310000000002"))).document == b"<ECF/>"
    references = [call.reference for call in signer.calls]
    assert references == ["E310000000001", None, "E310000000002"]  # every signing request


async def test_without_a_certificate_it_signs_nothing() -> None:
    signer = TestSigner(validity=None)
    with pytest.raises(SigningCertificateMissingError):
        await signer.sign_xml(CALL, document())
    with pytest.raises(SigningCertificateMissingError):
        await signer.sign_seed(CALL, seed())
    assert await signer.certificate_validity(CALL, TENANT) is None


async def test_a_document_signed_outside_its_certificate_s_validity_is_refused() -> None:
    signer = TestSigner(validity=VALIDITY)
    with pytest.raises(SigningCertificateNotValidError):
        await signer.sign_xml(CALL, document(signed_at=datetime(2027, 1, 2, tzinfo=UTC)))


async def test_a_seed_is_refused_when_its_certificate_does_not_cover_the_signer_s_clock() -> None:
    signer = TestSigner(validity=VALIDITY, clock=after_expiry)
    with pytest.raises(SigningCertificateNotValidError):
        await signer.sign_seed(CALL, seed())


async def test_a_validity_is_asked_for_a_tenant_in_a_mode() -> None:
    with pytest.raises(InvalidValueError, match="a tenant in a mode"):
        await TestSigner().certificate_validity(CALL, Tenant(TENANT.organization, TENANT.company, None))


def test_pytest_never_collects_it_as_a_test_class() -> None:
    assert TestSigner.__test__ is False


async def test_the_fixture_is_a_test_signer_of_its_own(test_signer: TestSigner) -> None:
    assert test_signer.calls == []
    assert (await test_signer.sign_xml(CALL, document())).certificate == DEFAULT_VALIDITY.certificate


# ── with_test_signer: an app whose SignerPort is the test's ─────────────────────


class StandIn:
    """What the installed signer binds: a signer that is never reached in the tests."""

    @facade
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        del call, request
        raise SignerUnavailableError

    @facade
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        del call, request
        raise SignerUnavailableError

    @facade
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        del call, tenant
        return None


def migrations(schema: str) -> SchemaMigrations:
    return SchemaMigrations(schema=schema, scripts="probe_unused:migrations")


def probe_module(name: str, *, provides: bool) -> Module:
    body = '[extension_points]\nprovides = ["SignerPort"]\n' if provides else ""
    package = f'[package]\nname = "{name}"\nkind = "module"\nsummary = "A probe."\nowners = ["@o"]\n\n'
    data = f'[dependencies]\npackages = ["kernel", "platform"]\n\n[data]\nschema = "{name}"\n\n'
    manifest = load_manifest(package + data + body)

    def assemble(context: ModuleContext) -> ModuleParts:
        bound = (SIGNER_PORT.bind(StandIn()),) if provides else ()
        return ModuleParts(provides=bound, migrations=migrations(context.database.schema))

    return Module(manifest=manifest, assemble=assemble)


def regime() -> RegimeDef:
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
        parameters={},
        sources=(
            Source(
                title="Probe law", url="https://law.invalid", version="1", retrieved_on=date(2026, 10, 13)
            ),
        ),
    )


def same(value: object) -> object:
    return value


def discovering(modules: Sequence[Module], seen: list[SignerPort]) -> Discover:
    def channel(context: ChannelContext) -> ChannelParts:
        seen.append(context.ports.get(SIGNER_PORT))
        return ChannelParts(channel="probe", migrations=migrations(context.database.schema))

    entry = ChannelEntry.model_validate(
        {
            "key": "us-probe-v1",
            "entry_point": "ledger_country_us.channels.probe:PACK",
            "schema": "us_probe",
            "valid_from": date(2026, 1, 1),
            "environments": ["test"],
            "document_kinds": ["invoice"],
            "directions": ["sales"],
            "tier": "bronze",
        }
    )
    pack = ChannelPack(entry=entry, regime="us", assemble=channel)
    groups: Mapping[str, tuple[Discovered, ...]] = {
        MODULES: tuple(Discovered(found.manifest.package.name, partial(same, found)) for found in modules),
        REGIMES: (Discovered("us", partial(same, regime())),),
        CHANNELS: (Discovered(pack.key, partial(same, pack)),),
    }

    def discover(group: str) -> Sequence[Discovered]:
        return groups[group]

    return discover


def test_an_app_binds_the_test_signer_where_the_installed_module_bound_its_own() -> None:
    signer = TestSigner()
    seen: list[SignerPort] = []
    modules = (probe_module("alpha", provides=False), probe_module("vault", provides=True))
    settings = Settings(environment="test", database_url=SecretStr(URL), database_direct_url=SecretStr(URL))
    App("worker", settings=settings, discover=with_test_signer(discovering(modules, seen), signer))
    assert seen == [signer]
    App("worker", settings=settings, discover=discovering(modules, seen))
    assert isinstance(seen[-1], StandIn)


def test_without_a_module_that_provides_a_signer_there_is_nothing_to_replace() -> None:
    discover = with_test_signer(discovering((probe_module("alpha", provides=False),), []), TestSigner())
    with pytest.raises(SignerProviderMissingError, match="SignerPort"):
        discover(MODULES)
    assert [found.name for found in discover(REGIMES)] == ["us"]
