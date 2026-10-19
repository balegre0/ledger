"""The channel's ``XmlSigner`` over fiscal-kit's ``SignerPort``: what reaches the port, what comes back."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, final, override
from uuid import UUID

import pytest

from ledger_country_do.channels.ecf_v1.adapters.signing.port import PortSigner
from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import SeedMalformedError
from ledger_country_do.channels.ecf_v1.application.emission import SigningSkewError
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    DocumentUnsignableError,
    SignerUnavailableError,
)
from ledger_country_do_testing.signatures import SHAPES
from ledger_fiscal_kit.ports import (
    SeedToSign,
    SignatureProfile,
    SignatureRefusedError,
    Signed,
    SignerPort,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_fiscal_kit.ports import SignerUnavailableError as PortUnavailableError
from ledger_kernel.errors import ConflictError, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
lazy from ledger_fiscal_kit.ports import CertificateValidity
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_platform.runtime.calls import SystemCall

TENANT: Final = Tenant(
    OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001")),
    CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002")),
    Mode.TEST,
)
CERTIFICATE: Final = UUID("0199e3a0-0000-7000-8000-000000000003")
SIGNED_AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
REASON: Final = "do-ecf-v1.emission"
DOCUMENT: Final = SHAPES["ecf"]
SEED: Final = SHAPES["seed"]


@final
@dataclass
class RecordingPort(SignerPort):
    """A ``SignerPort`` that records what reaches it and answers ``answer``, or raises ``refusal``."""

    refusal: LedgerError | None = None
    answer: bytes = b"<signed/>"
    calls: list[tuple[SystemCall, XmlToSign | SeedToSign]] = field(default_factory=list)

    def _answered(self, call: SystemCall, request: XmlToSign | SeedToSign) -> Signed:
        self.calls.append((call, request))
        if self.refusal is not None:
            raise self.refusal
        return Signed(document=self.answer, certificate=CERTIFICATE)

    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        return self._answered(call, request)

    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        return self._answered(call, request)

    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        raise AssertionError  # the channel never asks: validity at signing is the signer's


def signer(port: SignerPort) -> PortSigner:
    return PortSigner(port, tenant=TENANT, reason=REASON)


async def test_the_tenant_profile_and_reference_reach_the_port() -> None:
    port = RecordingPort()
    signed = await signer(port).sign_document(DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001")
    assert signed == b"<signed/>"
    [(call, request)] = port.calls
    assert (call.reason, call.scope) == (REASON, TENANT)
    assert request == XmlToSign(
        tenant=TENANT,
        profile=SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256,
        document=DOCUMENT,
        signed_at=SIGNED_AT,
        reference="E310000000001",
    )


async def test_each_signature_is_a_call_of_its_own() -> None:
    port = RecordingPort()
    channel = signer(port)
    await channel.sign_document(DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001")
    await channel.sign_seed(SEED, audience="ecf.dgii.gov.do")
    first, second = (call for call, _ in port.calls)
    assert first.call_id != second.call_id


async def test_a_seed_goes_as_a_seed() -> None:
    port = RecordingPort()
    assert await signer(port).sign_seed(SEED, audience="ecf.dgii.gov.do") == b"<signed/>"
    [(call, request)] = port.calls
    assert (call.reason, call.scope) == (REASON, TENANT)
    assert request == SeedToSign(
        tenant=TENANT,
        profile=SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256,
        seed=SEED,
        audience="ecf.dgii.gov.do",
    )


@pytest.mark.parametrize(
    "seed",
    [DOCUMENT, b"<a>", b'<!DOCTYPE a [<!ENTITY b "c">]><SemillaModel>&b;</SemillaModel>'],
    ids=["an-e-cf", "unreadable", "entity"],
)
async def test_what_is_not_dgii_s_seed_never_reaches_the_port(seed: bytes) -> None:
    # A hostile receiver must not get an arbitrary document signed as if it were a seed.
    port = RecordingPort()
    with pytest.raises(SeedMalformedError):
        await signer(port).sign_seed(seed, audience="receiver.example.do")
    assert port.calls == []


@pytest.mark.parametrize(
    ("refusal", "raised", "code", "kind"),
    [
        (SigningCertificateMissingError(), CertificateMissingError, "do_ecf_certificate_missing", "conflict"),
        (
            SigningCertificateNotValidError(),
            CertificateNotValidError,
            "do_ecf_certificate_not_valid",
            "conflict",
        ),
        # The channel's skew is retried (unavailable): the next signature states a fresh moment.
        (SigningClockSkewError(), SigningSkewError, "do_ecf_signing_skew", "unavailable"),
        (PortUnavailableError(), SignerUnavailableError, "do_ecf_signer_unavailable", "unavailable"),
        (
            SignatureRefusedError("xml_document_signed"),
            DocumentUnsignableError,
            "do_ecf_document_refused",
            "invalid",
        ),
    ],
    ids=["missing", "not-valid", "clock-skew", "unavailable", "refused"],
)
async def test_each_port_error_becomes_the_channel_s(
    refusal: LedgerError, raised: type[EcfError], code: str, kind: str
) -> None:
    channel = signer(RecordingPort(refusal=refusal))
    with pytest.raises(raised) as document:
        await channel.sign_document(DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001")
    with pytest.raises(raised) as seed:
        await channel.sign_seed(SEED, audience="ecf.dgii.gov.do")
    for caught in (document.value, seed.value):
        assert (caught.code, caught.kind, caught.__cause__) == (code, kind, refusal)


async def test_a_refusal_keeps_the_signer_s_reason() -> None:
    channel = signer(RecordingPort(refusal=SignatureRefusedError("xml_document_unsignable")))
    with pytest.raises(DocumentUnsignableError) as caught:
        await channel.sign_document(DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001")
    assert caught.value.reason == "xml_document_unsignable"
    assert str(caught.value) == "the signer refused to sign the document (xml_document_unsignable)"


async def test_an_error_the_port_does_not_declare_is_not_disguised() -> None:
    surprise = ConflictError("not one of the port's")
    with pytest.raises(ConflictError) as caught:
        await signer(RecordingPort(refusal=surprise)).sign_document(
            DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001"
        )
    assert caught.value is surprise


def test_a_signer_serves_a_tenant_in_a_mode() -> None:
    shared = Tenant(TENANT.organization, TENANT.company, None)
    with pytest.raises(InvalidValueError, match=r"port_signer.tenant must be a tenant in a mode"):
        PortSigner(RecordingPort(), tenant=shared, reason=REASON)


def test_the_channel_s_signer_errors_have_stable_kinds_and_codes() -> None:
    for error, kind, code in (
        (CertificateMissingError(), "conflict", "do_ecf_certificate_missing"),
        (CertificateNotValidError(), "conflict", "do_ecf_certificate_not_valid"),
        (SignerUnavailableError(), "unavailable", "do_ecf_signer_unavailable"),
        (DocumentUnsignableError("xml_document_signed"), "invalid", "do_ecf_document_refused"),
    ):
        assert (error.kind, error.code) == (kind, code)
        assert str(error)
