"""The channel's ``XmlSigner`` over fiscal-kit's ``SignerPort``: what reaches the port, what comes back."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, final, override
from uuid import UUID

import grpc
import pytest
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

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
    MAX_SEED_BYTES,
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
from ledger_platform.grpc.client import RemoteDefectError, RemoteForbiddenError, RemoteUnavailableError
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
OVERSIZED_SEED: Final = (
    b"<SemillaModel><valor>" + b"v" * MAX_SEED_BYTES + b"</valor><fecha>f</fecha></SemillaModel>"
)
"""DGII's shape, over the port's limit: refused as no seed, never as the port's internal error."""
SIGN_XML: Final = "/ledger.signer.v1.Signer/SignXml"


@final
@dataclass
class RecordingPort(SignerPort):
    """A ``SignerPort`` that records what reaches it and answers ``answer``, or raises ``refusal``."""

    refusal: Exception | None = None
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
    [DOCUMENT, b"<a>", b'<!DOCTYPE a [<!ENTITY b "c">]><SemillaModel>&b;</SemillaModel>', OVERSIZED_SEED],
    ids=["an-e-cf", "unreadable", "entity", "over-the-port-s-limit"],
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


@pytest.mark.parametrize(
    "failure",
    [
        RemoteUnavailableError(SIGN_XML, grpc.StatusCode.UNAVAILABLE, None),
        RemoteUnavailableError(SIGN_XML, grpc.StatusCode.DEADLINE_EXCEEDED, None),
        RemoteUnavailableError(SIGN_XML, grpc.StatusCode.INTERNAL, "signer_internal"),
        TimeoutError(),
    ],
    ids=["unavailable", "deadline", "remote-internal", "timeout"],
)
async def test_a_signer_that_cannot_answer_is_the_channel_s_signer_unavailable(failure: Exception) -> None:
    # A port client's transport failure is the port's unavailable: the emitter parks, never a defect.
    channel = signer(RecordingPort(refusal=failure))
    with pytest.raises(SignerUnavailableError) as document:
        await channel.sign_document(DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001")
    with pytest.raises(SignerUnavailableError) as seed:
        await channel.sign_seed(SEED, audience="ecf.dgii.gov.do")
    assert document.value.__cause__ is failure
    assert seed.value.__cause__ is failure


@pytest.mark.parametrize(
    "defect",
    [
        RemoteDefectError(SIGN_XML, grpc.StatusCode.INVALID_ARGUMENT, None),
        RemoteForbiddenError(SIGN_XML, grpc.StatusCode.PERMISSION_DENIED, "signer_method_not_allowed"),
    ],
    ids=["defect", "forbidden"],
)
async def test_a_call_the_signer_refuses_as_ours_is_a_defect_not_disguised(defect: Exception) -> None:
    with pytest.raises(type(defect)) as caught:
        await signer(RecordingPort(refusal=defect)).sign_document(
            DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001"
        )
    assert caught.value is defect


@pytest.mark.parametrize(
    "reason",
    ["", "Do ECF", "emission for 131098193@x", "a" * 65],
    ids=["empty", "spaces", "at-sign", "long"],
)
def test_a_reason_is_code_shaped(reason: str) -> None:
    # It becomes the audit's actor (system:<reason>): never personal data.
    with pytest.raises(InvalidValueError, match=r"port_signer.reason must be"):
        PortSigner(RecordingPort(), tenant=TENANT, reason=reason)


async def test_a_call_continues_the_current_trace() -> None:
    port = RecordingPort()
    channel = signer(port)
    await channel.sign_seed(SEED, audience="ecf.dgii.gov.do")
    context = SpanContext(
        trace_id=0x0AF7651916CD43DD8448EB211C80319C,
        span_id=0xB7AD6B7169203331,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    with trace.use_span(NonRecordingSpan(context)):
        await channel.sign_seed(SEED, audience="ecf.dgii.gov.do")
    outside, inside = (call.traceparent for call, _ in port.calls)
    assert outside is None
    assert inside == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
