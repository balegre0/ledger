"""The signer's messages: what the client sends and reads, and what an import's key keeps, on the wire.

Every enumeration's ``UNSPECIFIED`` (proto3's default, what a message that never set it holds) and any number
the contract does not name are refused, never read as a default; a moment is aware UTC both ways, and one
the signer did not set is refused rather than read as 1970.
"""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from typing import Final
lazy from collections.abc import Callable

import pytest

from ledger_fiscal_kit.ports import SeedToSign, SignatureProfile, Signed, XmlToSign
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.api import BackendKind, CertificateId, CertificateStatus, CertificateView
from ledger_signer.application.importing import ImportRequest
from ledger_signer.application.ports import ImportOutcome, Secret

ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
TENANT: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
CALL: Final = SystemCall(reason="ecf_emission", scope=TENANT, call_id="call-1")
AT: Final = datetime(2026, 10, 19, 12, 30, 15, 123456, tzinfo=UTC)
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
ACTIVE: Final = CertificateView(
    id=CertificateId(new_id()),
    organization=ORGANIZATION,
    company=COMPANY,
    modes=frozenset({Mode.TEST, Mode.LIVE}),
    backend=BackendKind.LOCAL,
    status=CertificateStatus.ACTIVE,
    fingerprint_sha256=bytes(range(32)),
    serial_number="0a1b2c",
    subject="CN=Juana Perez,serialNumber=00112345678",
    issuer="CN=Avansi CA,O=Avansi,C=DO",
    not_before=AT - timedelta(days=30),
    not_after=AT + timedelta(days=335),
    installed_at=AT,
    retired_at=None,
    retired_reason=None,
    version=1,
)
RETIRED: Final = dataclasses.replace(
    ACTIVE,
    modes=frozenset({Mode.LIVE}),
    backend=BackendKind.REMOTE,
    status=CertificateStatus.RETIRED,
    retired_at=AT + timedelta(hours=1),
    retired_reason="replaced",
    version=2,
)
REQUEST: Final = ImportRequest(tenant=TENANT, modes=frozenset({Mode.LIVE}), idempotency_key="import-0001")


def test_the_modes_go_both_ways_and_unspecified_is_refused() -> None:
    assert [codec.mode_of(codec.mode_wire(mode), "header.mode") for mode in Mode] == list(Mode)
    assert (codec.mode_wire(Mode.TEST), codec.mode_wire(Mode.LIVE)) == (
        signer_pb2.MODE_TEST,
        signer_pb2.MODE_LIVE,
    )
    for wire in (signer_pb2.MODE_UNSPECIFIED, signer_pb2.Mode.ValueType(7)):
        with pytest.raises(InvalidValueError, match=r"^signer\.header\.mode must be"):
            codec.mode_of(wire, "header.mode")


def test_the_backends_statuses_and_profiles_go_both_ways_and_unspecified_is_refused() -> None:
    assert [codec.backend_of(codec.backend_wire(kind)) for kind in BackendKind] == list(BackendKind)
    assert [codec.status_of(codec.status_wire(status)) for status in CertificateStatus] == list(
        CertificateStatus
    )
    assert codec.profile_of(codec.profile_wire(PROFILE)) is PROFILE
    assert codec.profile_wire(PROFILE) == signer_pb2.SIGNATURE_PROFILE_XMLDSIG_ENVELOPED_RSA_SHA256
    with pytest.raises(InvalidValueError, match=r"^signer\.certificate\.backend must be"):
        codec.backend_of(signer_pb2.BACKEND_UNSPECIFIED)
    with pytest.raises(InvalidValueError, match=r"^signer\.certificate\.status must be"):
        codec.status_of(signer_pb2.CERTIFICATE_STATUS_UNSPECIFIED)
    with pytest.raises(InvalidValueError, match=r"^signer\.profile must be"):
        codec.profile_of(signer_pb2.SIGNATURE_PROFILE_UNSPECIFIED)


def test_a_moment_is_aware_utc_both_ways_and_a_naive_one_is_never_sent() -> None:
    elsewhere = AT.astimezone(timezone(timedelta(hours=-4)))
    read = codec.moment_of(codec.timestamp(elsewhere))
    assert (read, read.tzinfo) == (AT, UTC)
    with pytest.raises(InvalidValueError, match=r"^signer\.moment must be an aware datetime"):
        codec.timestamp(AT.replace(tzinfo=None))


def test_an_id_that_is_not_a_uuid_is_refused_without_its_text() -> None:
    assert codec.id_of(str(COMPANY), "company.company_id") == COMPANY
    with pytest.raises(InvalidValueError, match=r"^signer\.company\.company_id must be a UUID$"):
        codec.id_of("4111111111111111", "company.company_id")


@pytest.mark.parametrize("certificate", [ACTIVE, RETIRED], ids=["active", "retired"])
def test_a_certificate_goes_both_ways(certificate: CertificateView) -> None:
    message = codec.certificate_wire(certificate)
    assert codec.certificate_view(message) == certificate
    assert (
        codec.certificate_view(signer_pb2.Certificate.FromString(message.SerializeToString())) == certificate
    )


def test_an_active_certificate_has_no_retirement_on_the_wire_and_its_modes_in_mode_order() -> None:
    message = codec.certificate_wire(ACTIVE)
    assert (message.HasField("retired_at"), message.retired_reason) == (False, "")
    assert list(message.modes) == [signer_pb2.MODE_TEST, signer_pb2.MODE_LIVE]


def without_not_before(message: signer_pb2.Certificate) -> str:
    message.ClearField("not_before")
    return "not_before"


def without_not_after(message: signer_pb2.Certificate) -> str:
    message.ClearField("not_after")
    return "not_after"


def without_installed_at(message: signer_pb2.Certificate) -> str:
    message.ClearField("installed_at")
    return "installed_at"


@pytest.mark.parametrize("clear", [without_not_before, without_not_after, without_installed_at])
def test_a_certificate_without_one_of_its_moments_is_refused(
    clear: Callable[[signer_pb2.Certificate], str],
) -> None:
    message = codec.certificate_wire(ACTIVE)
    unset = clear(message)
    with pytest.raises(InvalidValueError, match=rf"^signer\.certificate\.{unset} must be set$"):
        codec.certificate_view(message)


def test_a_certificate_of_a_company_without_ids_is_refused() -> None:
    message = codec.certificate_wire(ACTIVE)
    message.ClearField("company")
    with pytest.raises(
        InvalidValueError, match=r"^signer\.certificate\.company\.organization_id must be a UUID$"
    ):
        codec.certificate_view(message)


@pytest.mark.parametrize(
    "outcome",
    [ImportOutcome(view=ACTIVE, replayed=False), ImportOutcome(view=RETIRED, replayed=True)],
    ids=["installed", "replayed"],
)
def test_an_import_s_answer_goes_both_ways(outcome: ImportOutcome) -> None:
    response = codec.import_response(outcome)
    assert response.replayed is outcome.replayed
    assert codec.import_outcome(response) == outcome


def test_what_an_import_s_key_keeps_is_the_response_s_bytes() -> None:
    """Task 10's stored answer: the response of the import that installed (``replayed`` false), as bytes."""
    kept = codec.import_response(ImportOutcome(view=ACTIVE, replayed=False)).SerializeToString()
    read = signer_pb2.ImportCertificateResponse.FromString(kept)
    assert codec.import_outcome(read) == ImportOutcome(view=ACTIVE, replayed=False)
    assert (
        kept
        == signer_pb2.ImportCertificateResponse(
            certificate=codec.certificate_wire(ACTIVE)
        ).SerializeToString()
    )


def test_a_call_names_its_actor_and_its_call_id() -> None:
    context = codec.call_context(CALL)
    assert (context.call_id, context.actor) == ("call-1", "system:ecf_emission")


def test_a_document_to_sign_is_sent_with_its_tenant_profile_moment_and_reference() -> None:
    request = XmlToSign(
        tenant=TENANT, profile=PROFILE, document=b"<a/>", signed_at=AT, reference="E310000000001"
    )
    sent = codec.xml_request(CALL, request)
    assert (sent.call.actor, sent.company.organization_id, sent.company.company_id, sent.mode) == (
        "system:ecf_emission",
        str(ORGANIZATION),
        str(COMPANY),
        signer_pb2.MODE_TEST,
    )
    assert (sent.profile, sent.document, codec.moment_of(sent.signed_at), sent.reference) == (
        signer_pb2.SIGNATURE_PROFILE_XMLDSIG_ENVELOPED_RSA_SHA256,
        b"<a/>",
        AT,
        "E310000000001",
    )


def test_a_seed_to_sign_is_sent_with_its_audience() -> None:
    request = SeedToSign(tenant=TENANT, profile=PROFILE, seed=b"<s/>", audience="ecf.dgii.gov.do:443")
    sent = codec.seed_request(CALL, request)
    assert (
        sent.call.call_id,
        sent.company.company_id,
        sent.mode,
        sent.profile,
        sent.seed,
        sent.audience,
    ) == (
        "call-1",
        str(COMPANY),
        signer_pb2.MODE_TEST,
        signer_pb2.SIGNATURE_PROFILE_XMLDSIG_ENVELOPED_RSA_SHA256,
        b"<s/>",
        "ecf.dgii.gov.do:443",
    )


def test_a_signature_is_read_with_the_certificate_that_made_it() -> None:
    certificate = new_id()
    assert codec.signed_of(b"<a><Signature/></a>", str(certificate)) == Signed(
        document=b"<a><Signature/></a>", certificate=certificate
    )
    with pytest.raises(InvalidValueError, match=r"^signer\.signed\.certificate_id must be a UUID$"):
        codec.signed_of(b"<a/>", "")


def test_a_tenant_s_description_and_a_retirement_are_asked_by_company() -> None:
    described = codec.describe_request(CALL, TENANT, include_retired=True)
    assert (described.company.company_id, described.mode, described.include_retired) == (
        str(COMPANY),
        signer_pb2.MODE_TEST,
        True,
    )
    retired = codec.retire_request(CALL, Tenant(ORGANIZATION, COMPANY, None), ACTIVE.id)
    assert (retired.call.call_id, retired.company.organization_id, retired.certificate_id) == (
        "call-1",
        str(ORGANIZATION),
        str(ACTIVE.id),
    )


def test_a_tenant_without_a_mode_cannot_be_described() -> None:
    with pytest.raises(InvalidValueError, match=r"^signer\.tenant must be a tenant in a mode$"):
        codec.describe_request(CALL, Tenant(ORGANIZATION, COMPANY, None), include_retired=False)


def test_an_import_s_header_carries_its_source_and_never_shows_it() -> None:
    local = codec.local_header(CALL, REQUEST, Secret("bundle-password"))
    remote = codec.remote_header(CALL, REQUEST, Secret("entity-credential"))
    assert (local.WhichOneof("source"), local.local.password) == ("local", "bundle-password")
    assert (remote.WhichOneof("source"), remote.remote.credential) == ("remote", "entity-credential")
    assert (local.mode, list(local.modes), local.idempotency_key, local.company.company_id) == (
        signer_pb2.MODE_TEST,
        [signer_pb2.MODE_LIVE],
        "import-0001",
        str(COMPANY),
    )
    assert local.call.actor == "system:ecf_emission"


def test_the_methods_are_named_as_the_generated_descriptor_names_them() -> None:
    service = "/ledger.signer.v1.SignerService/"
    assert (
        service + "ImportCertificate",
        service + "DescribeCertificates",
        service + "RetireCertificate",
        service + "SignXml",
        service + "SignSeed",
    ) == (codec.IMPORT, codec.DESCRIBE, codec.RETIRE, codec.SIGN_XML, codec.SIGN_SEED)
