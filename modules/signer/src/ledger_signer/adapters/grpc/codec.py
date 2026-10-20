"""The signer's DTOs as ``ledger.signer.v1`` messages, and back: the one mapping of both sides of the wire.

The methods' full names are here too. The client builds its requests and reads the answers here, the
server reads requests and builds answers with the same functions, and an import's idempotency key keeps the
``ImportCertificateResponse`` bytes ``import_response`` makes (``replayed`` false), read back by
``import_outcome`` (plan Task 10's ``adapters.postgres.import_keys`` folds into these: its bytes are these).
The encoding is the contract's: changing what a message holds changes what replays of keys already stored
read, so a field is only ever added.

What comes off the wire is checked as our own code's output (``InvalidValueError``, a defect): an
enumeration's ``UNSPECIFIED`` (proto3's default, what a message that never set it holds) or a number the
contract does not name is refused, never read as a default; an id must be a UUID as ``str`` writes it; a
certificate's fingerprint must be a SHA-256, its modes distinct (one at least while active), its moments
must be set (an unset ``Timestamp`` reads as 1970). Moments are aware UTC both ways: a naive one is never
sent. Messages name the field, never the value: a password or a credential travels in a header.
"""

from typing import Final
from uuid import UUID
lazy from collections.abc import Iterable, Mapping
lazy from datetime import UTC, datetime

from google.protobuf.timestamp_pb2 import Timestamp

from ledger_fiscal_kit.ports import SignatureProfile, Signed
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode
from ledger_platform.runtime.calls import actor_of
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2
from ledger_signer.api import BackendKind, CertificateId, CertificateStatus, CertificateView
from ledger_signer.application.ports import ImportOutcome
from ledger_signer.domain.certificates import FINGERPRINT_BYTES
lazy from ledger_fiscal_kit.ports import SeedToSign, XmlToSign
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.data.audit import Actor
lazy from ledger_platform.runtime.calls import Call, SystemCall
lazy from ledger_signer.application.importing import ImportRequest
lazy from ledger_signer.application.ports import Secret

_SERVICE: Final = signer_pb2.DESCRIPTOR.services_by_name["SignerService"]


def _method(name: str) -> str:
    """``name``'s full name, ``/ledger.signer.v1.SignerService/<name>``; a name the contract lacks fails."""
    return f"/{_SERVICE.full_name}/{_SERVICE.methods_by_name[name].name}"


IMPORT: Final = _method("ImportCertificate")
DESCRIBE: Final = _method("DescribeCertificates")
RETIRE: Final = _method("RetireCertificate")
SIGN_XML: Final = _method("SignXml")
SIGN_SEED: Final = _method("SignSeed")
"""The methods' full names: what a call names (``RemoteError.rpc``) and what an allowlist names."""
_OWNER: Final = "signer"
"""What the fields of this module's errors are named under."""
MAX_CALL_TEXT: Final = 255
"""The longest actor or call id a call may name (what the audit keeps of who acted)."""
_MODES: Final[Mapping[Mode, signer_pb2.Mode.ValueType]] = {
    Mode.TEST: signer_pb2.MODE_TEST,
    Mode.LIVE: signer_pb2.MODE_LIVE,
}
_BACKENDS: Final[Mapping[BackendKind, signer_pb2.Backend.ValueType]] = {
    BackendKind.LOCAL: signer_pb2.BACKEND_LOCAL,
    BackendKind.REMOTE: signer_pb2.BACKEND_REMOTE,
}
_STATUSES: Final[Mapping[CertificateStatus, signer_pb2.CertificateStatus.ValueType]] = {
    CertificateStatus.ACTIVE: signer_pb2.CERTIFICATE_STATUS_ACTIVE,
    CertificateStatus.RETIRED: signer_pb2.CERTIFICATE_STATUS_RETIRED,
}
_PROFILES: Final[Mapping[SignatureProfile, signer_pb2.SignatureProfile.ValueType]] = {
    SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256: signer_pb2.SIGNATURE_PROFILE_XMLDSIG_ENVELOPED_RSA_SHA256,
}


def _invalid(*, field: str, requirement: str) -> InvalidValueError:
    return InvalidValueError(field=f"{_OWNER}.{field}", requirement=requirement)


def _member[V, W](table: Mapping[V, W], wire: W, field: str) -> V:
    """The value ``table`` sends as ``wire``; ``UNSPECIFIED`` and any number it does not send are refused."""
    for value, sent in table.items():
        if sent == wire:
            return value
    raise _invalid(field=field, requirement="a value the contract names, never UNSPECIFIED")


def mode_wire(mode: Mode) -> signer_pb2.Mode.ValueType:
    """``mode`` on the wire."""
    return _MODES[mode]


def mode_of(wire: signer_pb2.Mode.ValueType, field: str) -> Mode:
    """The mode ``wire`` names, read from ``field``."""
    return _member(_MODES, wire, field)


def modes_wire(modes: Iterable[Mode]) -> list[signer_pb2.Mode.ValueType]:
    """``modes`` on the wire, in ``Mode``'s order: one set of modes is always the same bytes."""
    chosen = frozenset(modes)
    return [wire for mode, wire in _MODES.items() if mode in chosen]


def backend_wire(backend: BackendKind) -> signer_pb2.Backend.ValueType:
    """``backend`` on the wire."""
    return _BACKENDS[backend]


def backend_of(wire: signer_pb2.Backend.ValueType) -> BackendKind:
    """The backend ``wire`` names."""
    return _member(_BACKENDS, wire, "certificate.backend")


def status_wire(status: CertificateStatus) -> signer_pb2.CertificateStatus.ValueType:
    """``status`` on the wire."""
    return _STATUSES[status]


def status_of(wire: signer_pb2.CertificateStatus.ValueType) -> CertificateStatus:
    """The status ``wire`` names."""
    return _member(_STATUSES, wire, "certificate.status")


def profile_wire(profile: SignatureProfile) -> signer_pb2.SignatureProfile.ValueType:
    """``profile`` on the wire."""
    return _PROFILES[profile]


def profile_of(wire: signer_pb2.SignatureProfile.ValueType) -> SignatureProfile:
    """The signature profile ``wire`` names."""
    return _member(_PROFILES, wire, "profile")


def timestamp(moment: datetime) -> Timestamp:
    """``moment`` (aware) on the wire; a naive one is refused, never taken as UTC."""
    if moment.utcoffset() is None:
        raise _invalid(field="moment", requirement="an aware datetime")
    stamp = Timestamp()
    stamp.FromDatetime(moment)
    return stamp


def moment_of(stamp: Timestamp) -> datetime:
    """The moment ``stamp`` holds, aware in UTC."""
    return stamp.ToDatetime(tzinfo=UTC)


def _required_moment(stamp: Timestamp, field: str, *, present: bool) -> datetime:
    if not present:
        raise _invalid(field=field, requirement="set")
    return moment_of(stamp)


def id_of(text: str, field: str) -> UUID:
    """The UUID ``text`` holds in its canonical form (as ``str`` writes it), read from ``field``.

    Any other text, another spelling of a UUID included, is refused without being quoted: one id is one text.
    """
    try:
        value = UUID(text)
    except ValueError:
        value = None
    if value is None or str(value) != text:
        raise _invalid(field=field, requirement="a UUID in its canonical form")
    return value


def _modes_of(message: signer_pb2.Certificate, status: CertificateStatus) -> frozenset[Mode]:
    """The modes ``message`` names: each once, and one at least while the certificate is active."""
    wires = list(message.modes)
    modes = frozenset(mode_of(wire, "certificate.modes") for wire in wires)
    if len(modes) != len(wires) or (status is CertificateStatus.ACTIVE and not modes):
        raise _invalid(field="certificate.modes", requirement="distinct modes, one at least while active")
    return modes


def _fingerprint_of(message: signer_pb2.Certificate) -> bytes:
    if len(message.fingerprint_sha256) != FINGERPRINT_BYTES:
        raise _invalid(field="certificate.fingerprint_sha256", requirement=f"{FINGERPRINT_BYTES} bytes")
    return message.fingerprint_sha256


def company_wire(organization: OrganizationId, company: CompanyId) -> signer_pb2.Company:
    """``company`` of ``organization`` on the wire."""
    return signer_pb2.Company(organization_id=str(organization), company_id=str(company))


def _company_of(message: signer_pb2.Company, field: str) -> tuple[OrganizationId, CompanyId]:
    return (
        OrganizationId(id_of(message.organization_id, f"{field}.organization_id")),
        CompanyId(id_of(message.company_id, f"{field}.company_id")),
    )


def _tenant_company(tenant: Tenant) -> signer_pb2.Company:
    return company_wire(tenant.organization, tenant.company)


def _tenant_mode(tenant: Tenant) -> signer_pb2.Mode.ValueType:
    mode = tenant.mode
    if mode is None:
        raise _invalid(field="tenant", requirement="a tenant in a mode")
    return mode_wire(mode)


def certificate_wire(view: CertificateView) -> signer_pb2.Certificate:
    """``view`` on the wire: no retirement while it is active."""
    return signer_pb2.Certificate(
        certificate_id=str(view.id),
        company=company_wire(view.organization, view.company),
        modes=modes_wire(view.modes),
        backend=backend_wire(view.backend),
        status=status_wire(view.status),
        fingerprint_sha256=view.fingerprint_sha256,
        serial_number=view.serial_number,
        subject=view.subject,
        issuer=view.issuer,
        not_before=timestamp(view.not_before),
        not_after=timestamp(view.not_after),
        installed_at=timestamp(view.installed_at),
        retired_at=None if view.retired_at is None else timestamp(view.retired_at),
        retired_reason=view.retired_reason or "",
        version=view.version,
    )


def certificate_view(message: signer_pb2.Certificate) -> CertificateView:
    """The certificate ``message`` describes (see the module for what is refused)."""
    organization, company = _company_of(message.company, "certificate.company")
    status = status_of(message.status)
    return CertificateView(
        id=CertificateId(id_of(message.certificate_id, "certificate.certificate_id")),
        organization=organization,
        company=company,
        modes=_modes_of(message, status),
        backend=backend_of(message.backend),
        status=status,
        fingerprint_sha256=_fingerprint_of(message),
        serial_number=message.serial_number,
        subject=message.subject,
        issuer=message.issuer,
        not_before=_required_moment(
            message.not_before, "certificate.not_before", present=message.HasField("not_before")
        ),
        not_after=_required_moment(
            message.not_after, "certificate.not_after", present=message.HasField("not_after")
        ),
        installed_at=_required_moment(
            message.installed_at, "certificate.installed_at", present=message.HasField("installed_at")
        ),
        retired_at=moment_of(message.retired_at) if message.HasField("retired_at") else None,
        retired_reason=message.retired_reason or None,
        version=message.version,
    )


def import_response(outcome: ImportOutcome) -> signer_pb2.ImportCertificateResponse:
    """An import's answer on the wire; with ``replayed`` false, what the import's key keeps (as bytes)."""
    return signer_pb2.ImportCertificateResponse(
        certificate=certificate_wire(outcome.view), replayed=outcome.replayed
    )


def import_outcome(response: signer_pb2.ImportCertificateResponse) -> ImportOutcome:
    """The import ``response`` answers."""
    return ImportOutcome(view=certificate_view(response.certificate), replayed=response.replayed)


def call_context(call: Call | SystemCall) -> signer_pb2.CallContext:
    """Who acts in ``call``, for the signer's audit: its actor and its call id."""
    actor = actor_of(call)
    return signer_pb2.CallContext(call_id=actor.call_id, actor=actor.name)


def _header(call: Call | SystemCall, request: ImportRequest) -> signer_pb2.ImportHeader:
    return signer_pb2.ImportHeader(
        call=call_context(call),
        company=_tenant_company(request.tenant),
        mode=_tenant_mode(request.tenant),
        modes=modes_wire(request.modes),
        idempotency_key=request.idempotency_key,
    )


def local_header(
    call: Call | SystemCall, request: ImportRequest, password: Secret
) -> signer_pb2.ImportHeader:
    """The header of an import of a bundle (its chunks follow), opened with ``password``.

    The header holds the password: it goes to grpc, never to a log or a ``repr``.
    """
    header = _header(call, request)
    header.local.password = password.reveal()
    return header


def remote_header(
    call: Call | SystemCall, request: ImportRequest, credential: Secret
) -> signer_pb2.ImportHeader:
    """The header (the whole request) of an import of the certificate an entity signs with for ``credential``.

    The header holds the credential: it goes to grpc, never to a log or a ``repr``.
    """
    header = _header(call, request)
    header.remote.credential = credential.reveal()
    return header


def describe_request(
    call: Call | SystemCall, tenant: Tenant, *, include_retired: bool
) -> signer_pb2.DescribeCertificatesRequest:
    """A description of the certificates serving ``tenant`` (in a mode)."""
    return signer_pb2.DescribeCertificatesRequest(
        call=call_context(call),
        company=_tenant_company(tenant),
        mode=_tenant_mode(tenant),
        include_retired=include_retired,
    )


def retire_request(
    call: Call | SystemCall, company: Tenant, certificate: CertificateId
) -> signer_pb2.RetireCertificateRequest:
    """The retirement of ``certificate`` of ``company``, for every mode: no mode is sent."""
    return signer_pb2.RetireCertificateRequest(
        call=call_context(call), company=_tenant_company(company), certificate_id=str(certificate)
    )


def xml_request(call: Call | SystemCall, request: XmlToSign) -> signer_pb2.SignXmlRequest:
    """``request`` on the wire."""
    return signer_pb2.SignXmlRequest(
        call=call_context(call),
        company=_tenant_company(request.tenant),
        mode=_tenant_mode(request.tenant),
        profile=profile_wire(request.profile),
        document=request.document,
        signed_at=timestamp(request.signed_at),
        reference=request.reference,
    )


def seed_request(call: Call | SystemCall, request: SeedToSign) -> signer_pb2.SignSeedRequest:
    """``request`` on the wire."""
    return signer_pb2.SignSeedRequest(
        call=call_context(call),
        company=_tenant_company(request.tenant),
        mode=_tenant_mode(request.tenant),
        profile=profile_wire(request.profile),
        seed=request.seed,
        audience=request.audience,
    )


def signed_of(document: bytes, certificate_id: str) -> Signed:
    """A signed document (or seed) and the certificate the signer says made it."""
    return Signed(document=document, certificate=id_of(certificate_id, "signed.certificate_id"))


def call_actor(context: signer_pb2.CallContext) -> Actor:
    """Who acts in the call ``context`` names, for the signer's audit: its actor and its call id, both set."""
    for field, value in (("call_id", context.call_id), ("actor", context.actor)):
        if not 0 < len(value) <= MAX_CALL_TEXT:
            raise _invalid(field=f"call.{field}", requirement=f"1 to {MAX_CALL_TEXT} characters")
    return Actor(context.actor, context.call_id)


def _tenant_of(company: signer_pb2.Company, mode: signer_pb2.Mode.ValueType | None, field: str) -> Tenant:
    """The tenant ``company`` names, in the mode ``mode`` names (``None``: a company's, in no mode)."""
    organization, company_id = _company_of(company, f"{field}.company")
    return Tenant(organization, company_id, None if mode is None else mode_of(mode, f"{field}.mode"))


def xml_to_sign(message: signer_pb2.SignXmlRequest) -> XmlToSign:
    """The document ``message`` asks to sign: its tenant, profile, moment (set) and reference."""
    return XmlToSign(
        tenant=_tenant_of(message.company, message.mode, "sign_xml"),
        profile=profile_of(message.profile),
        document=message.document,
        signed_at=_required_moment(
            message.signed_at, "sign_xml.signed_at", present=message.HasField("signed_at")
        ),
        reference=message.reference,
    )


def seed_to_sign(message: signer_pb2.SignSeedRequest) -> SeedToSign:
    """The seed ``message`` asks to sign: its tenant, profile and audience."""
    return SeedToSign(
        tenant=_tenant_of(message.company, message.mode, "sign_seed"),
        profile=profile_of(message.profile),
        seed=message.seed,
        audience=message.audience,
    )


def described(message: signer_pb2.DescribeCertificatesRequest) -> Tenant:
    """The tenant (in a mode) whose certificates ``message`` asks for."""
    return _tenant_of(message.company, message.mode, "describe")


def retirement(message: signer_pb2.RetireCertificateRequest) -> tuple[Tenant, CertificateId]:
    """The company (no mode: a retirement is for every mode) and the certificate ``message`` retires."""
    company = _tenant_of(message.company, None, "retire")
    return company, CertificateId(id_of(message.certificate_id, "retire.certificate_id"))


def import_request(header: signer_pb2.ImportHeader) -> ImportRequest:
    """The import ``header`` asks for: its tenant in the request's mode, the modes (each once) and its key."""
    wires = list(header.modes)
    modes = frozenset(mode_of(wire, "import_header.modes") for wire in wires)
    if len(modes) != len(wires):
        raise _invalid(field="import_header.modes", requirement="each mode named once")
    return ImportRequest(
        tenant=_tenant_of(header.company, header.mode, "import_header"),
        modes=modes,
        idempotency_key=header.idempotency_key,
    )
