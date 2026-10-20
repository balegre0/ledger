import hashlib
import inspect
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
from uuid import UUID
lazy from datetime import date

import pytest

from ledger_fiscal_kit.ports import (
    ARCHIVE_PORT,
    COMPANY_DIRECTORY,
    MAX_SEED_BYTES,
    SIGNER_PORT,
    ArchiveDigestMismatchError,
    ArchivedObject,
    ArchiveObjectMissingError,
    ArchivePort,
    ArchiveUnavailableError,
    CertificateValidity,
    CompanyDirectory,
    ReceivingCompany,
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
    archive_key,
    is_audience,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.auth.access import PermissionSource
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.keys import BindingTypeError
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.errors import LedgerError

TENANT: Final = Tenant(
    OrganizationId(UUID("0199e3a0-0000-7000-8000-000000000001")),
    CompanyId(UUID("0199e3a0-0000-7000-8000-000000000002")),
    Mode.TEST,
)
CERTIFICATE: Final = UUID("0199e3a0-0000-7000-8000-000000000003")
SIGNED_AT: Final = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
DOCUMENT: Final = b"<ECF><RNCEmisor>101850043</RNCEmisor></ECF>"
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256


@final
class Echo(SignerPort):
    """A port that answers what it is given, unsigned: enough to be recognized by the key."""

    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        return Signed(document=request.document, certificate=CERTIFICATE)

    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        return Signed(document=request.seed, certificate=CERTIFICATE)

    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        return None


XML: Final = XmlToSign(
    tenant=TENANT, profile=PROFILE, document=DOCUMENT, signed_at=SIGNED_AT, reference="E310000000001"
)
SEED: Final = SeedToSign(tenant=TENANT, profile=PROFILE, seed=b"<SemillaModel/>", audience="ecf.dgii.gov.do")
REFERENCE_RULE: Final = "xml_to_sign.reference must be 1 to 64 characters of A-Z a-z 0-9 . _ : -"


def test_signer_port_is_recognized_by_its_key() -> None:
    port = Echo()
    assert SIGNER_PORT.name == "SignerPort"
    assert SIGNER_PORT.take(port) is port
    assert SIGNER_PORT.bind(port).value is port
    with pytest.raises(BindingTypeError):
        SIGNER_PORT.take(object())


async def test_the_echo_port_answers_with_what_it_was_given() -> None:
    port = Echo()
    call = SystemCall(reason="test", scope=TENANT)
    assert (await port.sign_xml(call, XML)).document == DOCUMENT
    assert (await port.sign_seed(call, SEED)).certificate == CERTIFICATE
    assert await port.certificate_validity(call, TENANT) is None


@pytest.mark.parametrize(
    "reference",
    ["E310000000001", "a", "x" * 64, "receipt:2026-10-13.01_A"],
)
def test_a_plain_reference_is_kept(reference: str) -> None:
    assert replace(XML, reference=reference).reference == reference


@pytest.mark.parametrize(
    "reference",
    ["", "x" * 65, "E31 0001", "juan@example.com", "E31\n", "Peña"],
    ids=["empty", "too-long", "space", "at-sign", "newline", "non-ascii"],
)
def test_a_reference_is_short_and_plain(reference: str) -> None:
    with pytest.raises(InvalidValueError) as caught:
        replace(XML, reference=reference)
    assert str(caught.value) == REFERENCE_RULE


def test_a_naive_signed_at_is_refused() -> None:
    with pytest.raises(InvalidValueError, match=r"xml_to_sign.signed_at must be an aware datetime"):
        replace(XML, signed_at=SIGNED_AT.replace(tzinfo=None))


def test_a_request_names_a_mode() -> None:
    shared = Tenant(TENANT.organization, TENANT.company, None)
    with pytest.raises(InvalidValueError, match=r"xml_to_sign.tenant must be a tenant in a mode"):
        replace(XML, tenant=shared)
    with pytest.raises(InvalidValueError, match=r"seed_to_sign.tenant must be a tenant in a mode"):
        replace(SEED, tenant=shared)


def test_a_seed_over_the_limit_is_refused() -> None:
    assert MAX_SEED_BYTES == 16 * 1024
    assert len(replace(SEED, seed=b"x" * MAX_SEED_BYTES).seed) == MAX_SEED_BYTES
    with pytest.raises(InvalidValueError, match=rf"seed_to_sign.seed must be at most {MAX_SEED_BYTES} bytes"):
        replace(SEED, seed=b"x" * (MAX_SEED_BYTES + 1))


@pytest.mark.parametrize("audience", ["ecf.dgii.gov.do", "10.0.0.1:443", "[::1]:8443", "a"])
def test_an_audience_is_a_host(audience: str) -> None:
    assert replace(SEED, audience=audience).audience == audience
    assert is_audience(audience)


@pytest.mark.parametrize(
    "audience", ["", "x" * 254, "ecf.dgii.gov.do/path", "a b"], ids=["empty", "too-long", "path", "space"]
)
def test_an_audience_that_is_not_a_host_is_refused(audience: str) -> None:
    with pytest.raises(InvalidValueError, match=r"seed_to_sign.audience"):
        replace(SEED, audience=audience)
    assert not is_audience(audience)


def test_requests_show_no_document() -> None:
    signed = Signed(document=DOCUMENT, certificate=CERTIFICATE)
    for shown in (repr(XML), repr(SEED), repr(signed)):
        assert "101850043" not in shown
        assert "SemillaModel" not in shown
    assert "E310000000001" in repr(XML)
    assert str(CERTIFICATE) in repr(signed)


def test_a_validity_is_an_ordered_pair_of_aware_moments() -> None:
    validity = CertificateValidity(
        certificate=CERTIFICATE, not_before=SIGNED_AT, not_after=SIGNED_AT + timedelta(days=365)
    )
    assert validity.not_after - validity.not_before == timedelta(days=365)
    with pytest.raises(InvalidValueError, match=r"certificate_validity.not_before must be an aware datetime"):
        CertificateValidity(
            certificate=CERTIFICATE, not_before=SIGNED_AT.replace(tzinfo=None), not_after=SIGNED_AT
        )
    with pytest.raises(InvalidValueError, match=r"certificate_validity.not_after must be an aware datetime"):
        CertificateValidity(
            certificate=CERTIFICATE, not_before=SIGNED_AT, not_after=SIGNED_AT.replace(tzinfo=None)
        )
    with pytest.raises(InvalidValueError, match=r"not_after must be no earlier than not_before"):
        CertificateValidity(
            certificate=CERTIFICATE, not_before=SIGNED_AT, not_after=SIGNED_AT - timedelta(seconds=1)
        )


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (SignerUnavailableError(), ErrorKind.UNAVAILABLE, "signer_unavailable"),
        (SigningCertificateMissingError(), ErrorKind.CONFLICT, "signing_certificate_missing"),
        (SigningCertificateNotValidError(), ErrorKind.CONFLICT, "signing_certificate_not_valid"),
        (SigningClockSkewError(), ErrorKind.CONFLICT, "signing_clock_skew"),
        (SignatureRefusedError("xml_document_signed"), ErrorKind.INVALID, "signature_refused"),
    ],
)
def test_the_port_s_errors_have_stable_kinds_and_codes(
    error: LedgerError, kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)
    assert str(error)


def test_a_refusal_keeps_the_signer_s_code_as_its_reason() -> None:
    refused = SignatureRefusedError("xml_document_unsignable")
    assert refused.reason == "xml_document_unsignable"
    assert "xml_document_unsignable" in str(refused)
    with pytest.raises(InvalidValueError, match=r"signature_refused.reason must be a snake_case code"):
        SignatureRefusedError("Bearer s3cr3t <ECF/>")


def test_a_validity_covers_both_its_ends() -> None:
    validity = CertificateValidity(
        certificate=CERTIFICATE, not_before=SIGNED_AT, not_after=SIGNED_AT + timedelta(days=365)
    )
    assert validity.covers(SIGNED_AT)
    assert validity.covers(validity.not_after)
    assert not validity.covers(SIGNED_AT - timedelta(microseconds=1))
    assert not validity.covers(validity.not_after + timedelta(microseconds=1))
    with pytest.raises(InvalidValueError, match=r"certificate_validity.covers must be an aware datetime"):
        validity.covers(SIGNED_AT.replace(tzinfo=None))


RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
RECEIVING: Final = ReceivingCompany(
    organization=TENANT.organization,
    company=TENANT.company,
    mode=Mode.TEST,
    legal_name="Ferretería Duarte SRL",
    tax_id=RNC,
)


@final
class Directory(CompanyDirectory):
    """A directory, as far as its key can tell: what it answers is never asked."""

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        return frozenset()

    @override
    async def receiving_company(
        self, call: SystemCall, *, tax_id: TaxId, channel: str, environment: str
    ) -> ReceivingCompany | None:
        return None

    @override
    async def company(self, call: SystemCall, company: CompanyId) -> ReceivingCompany:
        return RECEIVING


@final
class Shelf(ArchivePort):
    """An archive, as far as its key can tell: what it answers is never asked."""

    @override
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        return ArchivedObject(key=f"{key}@{retain_until}", sha256=bytes(32), size=len(content))

    @override
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        return b""


def keyword_only(member: object) -> list[str]:
    """The keyword-only parameters of ``member``, a callable, in order."""
    assert callable(member)
    parameters = inspect.signature(member).parameters.values()
    return [parameter.name for parameter in parameters if parameter.kind is inspect.Parameter.KEYWORD_ONLY]


def test_a_company_directory_is_recognized_by_its_key_and_is_a_permission_source() -> None:
    directory = Directory()
    assert COMPANY_DIRECTORY.name == "CompanyDirectory"
    assert COMPANY_DIRECTORY.take(directory) is directory
    assert PermissionSource in type(directory).__mro__
    with pytest.raises(BindingTypeError):
        COMPANY_DIRECTORY.take(Echo())
    assert CompanyDirectory.__abstractmethods__ == {"permissions", "receiving_company", "company"}
    assert keyword_only(directory.receiving_company) == ["tax_id", "channel", "environment"]
    assert keyword_only(directory.company) == []


def test_a_receiving_company_shows_neither_its_name_nor_its_tax_id() -> None:
    shown = repr(RECEIVING)
    assert "Duarte" not in shown
    assert "101850043" not in shown
    assert (RECEIVING.legal_name, RECEIVING.tax_id, RECEIVING.tenant) == (
        "Ferretería Duarte SRL",
        RNC,
        TENANT,
    )


def test_an_archive_is_recognized_by_its_key() -> None:
    shelf = Shelf()
    assert ARCHIVE_PORT.name == "ArchivePort"
    assert ARCHIVE_PORT.take(shelf) is shelf
    with pytest.raises(BindingTypeError):
        ARCHIVE_PORT.take(Directory())
    assert ArchivePort.__abstractmethods__ == {"put_once", "get"}
    assert keyword_only(shelf.put_once) == ["retain_until", "media_type"]
    assert keyword_only(shelf.get) == ["sha256"]


def test_an_archived_object_names_a_sha256_and_a_size() -> None:
    stored = ArchivedObject(key="k", sha256=bytes(32), size=0)
    assert (stored.key, stored.sha256, stored.size) == ("k", bytes(32), 0)
    with pytest.raises(
        InvalidValueError, match=r"archived_object.sha256 must be a SHA-256 digest \(32 bytes\)"
    ):
        ArchivedObject(key="k", sha256=b"short", size=0)
    with pytest.raises(InvalidValueError, match=r"archived_object.size must be 0 or more"):
        ArchivedObject(key="k", sha256=bytes(32), size=-1)


def test_an_archive_key_names_where_the_bytes_belong_and_what_they_are() -> None:
    digest = hashlib.sha256(DOCUMENT).hexdigest()
    key = archive_key(channel="do-ecf-v1", tenant=TENANT, kind="ecf", content=DOCUMENT, extension="xml")
    assert key == f"do-ecf-v1/test/{TENANT.organization}/{TENANT.company}/ecf/{digest}.xml"
    live = replace(TENANT, mode=Mode.LIVE)
    receipt = archive_key(
        channel="do-ecf-v1", tenant=live, kind="arecf-refused", content=b"", extension="xml"
    )
    assert receipt.startswith(f"do-ecf-v1/live/{TENANT.organization}/{TENANT.company}/arecf-refused/")


@pytest.mark.parametrize("kind", ["ecf", "representation", "a", "x" * 32, "arecf-refused", "folio2"])
def test_a_kind_is_a_plain_lower_case_word(kind: str) -> None:
    key = archive_key(channel="do-ecf-v1", tenant=TENANT, kind=kind, content=DOCUMENT, extension="xml")
    assert f"/{kind}/" in key


KIND_RULE: Final = "archive_key.kind must be 1 to 32 lower-case letters, digits or hyphens, led by a letter"
EXTENSION_RULE: Final = "archive_key.extension must be 1 to 8 lower-case letters or digits"


@pytest.mark.parametrize(
    ("channel", "kind", "extension", "rule"),
    [
        ("do-ecf-v1", "../ecf", "xml", KIND_RULE),
        ("do-ecf-v1", "ecf/..", "xml", KIND_RULE),
        ("do-ecf-v1", "", "xml", KIND_RULE),
        ("do-ecf-v1", "Ecf", "xml", KIND_RULE),
        ("do-ecf-v1", "-ecf", "xml", KIND_RULE),
        ("do-ecf-v1", "x" * 33, "xml", KIND_RULE),
        ("../etc", "ecf", "xml", "archive_key.channel must be a channel key"),
        ("do-ecf-v1/..", "ecf", "xml", "archive_key.channel must be a channel key"),
        ("do-ecf-v1", "ecf", "x/ml", EXTENSION_RULE),
        ("do-ecf-v1", "ecf", "", EXTENSION_RULE),
    ],
)
def test_an_archive_key_is_built_from_known_parts_only(
    channel: str, kind: str, extension: str, rule: str
) -> None:
    with pytest.raises(InvalidValueError, match=re.escape(rule)):
        archive_key(channel=channel, tenant=TENANT, kind=kind, content=DOCUMENT, extension=extension)


def test_an_archive_key_belongs_to_a_mode() -> None:
    shared = Tenant(TENANT.organization, TENANT.company, None)
    with pytest.raises(InvalidValueError, match=r"archive_key.tenant must be a tenant in a mode"):
        archive_key(channel="do-ecf-v1", tenant=shared, kind="ecf", content=DOCUMENT, extension="xml")


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (ArchiveUnavailableError(), ErrorKind.UNAVAILABLE, "archive_unavailable"),
        (ArchiveDigestMismatchError(), ErrorKind.INTERNAL, "archive_digest_mismatch"),
        (ArchiveObjectMissingError(), ErrorKind.INTERNAL, "archive_object_missing"),
    ],
)
def test_the_archive_s_errors_have_stable_kinds_and_codes(
    error: LedgerError, kind: ErrorKind, code: str
) -> None:
    assert (error.kind, error.code) == (kind, code)
    assert str(error)
