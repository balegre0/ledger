"""The signer's gRPC service over in-memory ports, served on port 0 behind the platform's guard.

What the clients of Task 11 send reaches the use cases as the caller's actor (``acting_as``) and, for a
signature, as its caller (``current_caller``); an import streams its header first, then its bundle, bounded;
a request the service cannot read is malformed, said without its value; the use cases' refusals cross with
their codes, a damaged custody as a defect saying no more; and no password, credential or document reaches a
log line, a span, an audit row, an event or an answer (Review Focus 3).
"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, final
lazy from collections.abc import AsyncGenerator, Mapping
lazy from uuid import UUID

import grpc
import pytest
from cryptography.hazmat.primitives import hashes
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry
from pydantic import SecretStr
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace import ReadableSpan

from ledger_fiscal_kit.ports import SeedToSign, SignatureProfile, SigningCertificateMissingError, XmlToSign
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.audit import Actor
from ledger_platform.data.codec import payload_of
from ledger_platform.grpc import (
    CallerPolicy,
    CallerTokens,
    MalformedRequestError,
    RemoteError,
    call_stream_unary,
    call_unary,
)
from ledger_platform.grpc.client import INTERNAL_CODE, RemoteDefectError
from ledger_platform.grpc.guard import MethodNotAllowedError
from ledger_platform.grpc.metadata import CALL_ID_KEY, call_metadata
from ledger_platform.grpc.server import ServerOptions
from ledger_platform.runtime.calls import SystemCall
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.client import SignerClient, SignerDefectError
from ledger_signer.adapters.grpc.codec import (
    ImportChunksUnexpectedError,
    ImportHeaderFirstError,
    ImportTooSlowError,
)
from ledger_signer.adapters.grpc.server import (
    CALLER_METHODS,
    SignerNotServingError,
    SignerServer,
    SignerServicer,
)
from ledger_signer.adapters.settings import (
    IMPORT_DEADLINE_MARGIN_SECONDS,
    MAX_DEADLINE_SECONDS,
    MAX_IMPORT_DEADLINE_SECONDS,
)
from ledger_signer.api import CertificateStatus
from ledger_signer.application.certificates import Describing, Retiring
from ledger_signer.application.importing import (
    MAX_BUNDLE_BYTES,
    Importing,
    ImportRequest,
    ImportTooLargeError,
)
from ledger_signer.application.ports import BundleUnreadableError, Secret, SignerRules
from ledger_signer.application.signing import Signing
from ledger_signer.domain.certificates import BackendKind
from ledger_testing.pki import bundle
lazy from ledger_kernel.clock import Clock
lazy from ledger_signer.adapters.grpc._generated.v1.signer_pb2_grpc import SignerServiceAsyncStub
lazy from ledger_signer.application.ports import Custody, SignerUnits
lazy from ledger_signer.events import CertificateEvent
lazy from ledger_testing.pki import Issued

TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}
ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
IN_TEST: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
OWNER: Final = Tenant(ORGANIZATION, COMPANY, None)
CALL: Final = SystemCall(reason="certificates", scope=IN_TEST, call_id="call-7")
ACTOR: Final = Actor("system:certificates", "call-7")
PASSWORD: Final = "PASSWORD-SENTINEL"
CREDENTIAL: Final = "CREDENTIAL-SENTINEL"
DOCUMENT: Final = b"<ECF><Encabezado><Nombre>DOCUMENT-SENTINEL</Nombre></Encabezado></ECF>"
SENTINELS: Final = (PASSWORD, CREDENTIAL, "DOCUMENT-SENTINEL")
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
REQUEST: Final = ImportRequest(tenant=IN_TEST, modes=frozenset({Mode.TEST}), idempotency_key="import-0001")
CHUNK: Final = 64 * 1024
DEADLINE: Final = MAX_DEADLINE_SECONDS
"""What a call waits, as in production: a success path never races a wall clock."""
IMPORT_DEADLINE: Final = MAX_IMPORT_DEADLINE_SECONDS + IMPORT_DEADLINE_MARGIN_SECONDS
"""What an import's call waits, as the api's client does: past the service's own patience, to hear it."""
IMPORT_SECONDS: Final = 10.0
"""How long the service waits for an import's stream to end: generous (a stream held open has its own)."""
HELD_IMPORT_SECONDS: Final = 0.5
"""How long the service waits for the stream a test holds open."""
NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)


class Audited(Protocol):
    """An audit row the stand-in units kept."""

    @property
    def actor(self) -> Actor: ...
    @property
    def action(self) -> str: ...
    @property
    def detail(self) -> Mapping[str, str | int]: ...


class Store(Protocol):
    """What the stand-in units committed."""

    rows: Mapping[UUID, object]
    audits: list[Audited]
    events: list[CertificateEvent]


class Damageable(Protocol):
    """The stand-in custody's switch."""

    damaged: bool


class Moving(Protocol):
    """The stand-in clock."""

    now: datetime


@final
@dataclass(frozen=True, slots=True)
class Served:
    """The service on port 0, and the spans its guard made."""

    address: str
    spans: InMemorySpanExporter


def callers() -> CallerTokens:
    """Each caller's token, with the service's allowlists."""
    return CallerTokens(
        [
            CallerPolicy(name=name, tokens=(SecretStr(TOKENS[name]),), methods=methods)
            for name, methods in CALLER_METHODS.items()
        ]
    )


@pytest.fixture
def import_seconds() -> float:
    """The service's patience with an import's stream (a test parametrizes its own)."""
    return IMPORT_SECONDS


@pytest.fixture
def servicer(units: SignerUnits, custody: Custody, clock: Clock, import_seconds: float) -> SignerServicer:
    """The service over the stand-ins, offering both backends."""
    rules = SignerRules(
        backends=frozenset(BackendKind),
        skew=timedelta(seconds=300),
        concurrency=2,
        import_concurrency=2,
        notice_days=(30, 7),
    )
    return SignerServicer(
        importing=Importing(units=units, custody=custody, rules=rules, clock=clock),
        describing=Describing(units),
        retiring=Retiring(units),
        signing=Signing(units=units, custody=custody, rules=rules, clock=clock),
        import_seconds=import_seconds,
    )


@pytest.fixture
async def served(servicer: SignerServicer) -> AsyncGenerator[Served]:
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    server = SignerServer(
        servicer,
        port=0,
        registry=CollectorRegistry(),
        callers=callers(),
        options=ServerOptions(grace=0.1, tracer=provider.get_tracer("signer-tests")),
    )
    async with server:
        yield Served(f"127.0.0.1:{server.port}", spans)


def client_of(served: Served, caller: str) -> SignerClient:
    return SignerClient(
        served.address,
        SecretStr(TOKENS[caller]),
        deadline=MAX_DEADLINE_SECONDS,
        import_deadline=MAX_IMPORT_DEADLINE_SECONDS,
    )


@pytest.fixture
async def api(served: Served) -> AsyncGenerator[SignerClient]:
    async with client_of(served, "api") as client:
        yield client


@pytest.fixture
async def worker(served: Served) -> AsyncGenerator[SignerClient]:
    async with client_of(served, "worker") as client:
        yield client


@final
@dataclass(frozen=True, slots=True)
class Clients:
    """The api's and the worker's clients of the service."""

    api: SignerClient
    worker: SignerClient


@pytest.fixture
def clients(api: SignerClient, worker: SignerClient) -> Clients:
    return Clients(api, worker)


@pytest.fixture
async def stub(served: Served) -> AsyncGenerator[SignerServiceAsyncStub]:
    """A stub of the service sending whatever a test builds."""
    async with grpc.aio.insecure_channel(served.address) as channel:
        yield signer_pb2_grpc.SignerServiceStub(channel)


def sent(caller: str, call_id: str = CALL.call_id) -> tuple[tuple[str, str], ...]:
    """What a call of ``caller`` carries: its token and a call id (``CALL``'s unless told otherwise)."""
    return call_metadata(SecretStr(TOKENS[caller]), call_id=call_id)


async def parts_of(data: bytes, size: int = CHUNK) -> AsyncGenerator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def stream(
    *requests: signer_pb2.ImportCertificateRequest,
) -> AsyncGenerator[signer_pb2.ImportCertificateRequest]:
    for request in requests:
        yield request


def local() -> signer_pb2.ImportCertificateRequest:
    return signer_pb2.ImportCertificateRequest(header=codec.local_header(CALL, REQUEST, Secret(PASSWORD)))


def remote() -> signer_pb2.ImportCertificateRequest:
    return signer_pb2.ImportCertificateRequest(header=codec.remote_header(CALL, REQUEST, Secret(CREDENTIAL)))


def chunk(data: bytes = b"\x30\x82") -> signer_pb2.ImportCertificateRequest:
    return signer_pb2.ImportCertificateRequest(chunk=data)


async def refused_import(
    stub: SignerServiceAsyncStub, *requests: signer_pb2.ImportCertificateRequest
) -> RemoteError:
    with pytest.raises(RemoteError) as caught:
        await call_stream_unary(
            stub.ImportCertificate,
            stream(*requests),
            rpc=codec.IMPORT,
            deadline=IMPORT_DEADLINE,
            metadata=sent("api"),
        )
    return caught.value


async def installed(api: SignerClient, holder: Issued) -> UUID:
    """``holder``'s bundle imported by the api for test mode: the certificate's id."""
    data = bundle(holder, password=PASSWORD)
    outcome = await api.import_local(CALL, REQUEST, Secret(PASSWORD), parts_of(data, 512))
    return outcome.view.id


def document(at: datetime) -> XmlToSign:
    return XmlToSign(
        tenant=IN_TEST, profile=PROFILE, document=DOCUMENT, signed_at=at, reference="E310000000001"
    )


def test_the_callers_may_call_what_decision_12_says() -> None:
    assert {
        "api": {codec.IMPORT, codec.DESCRIBE, codec.RETIRE, codec.SIGN_XML},
        "worker": {codec.SIGN_XML, codec.SIGN_SEED, codec.DESCRIBE},
        "receiver": {codec.SIGN_XML, codec.SIGN_SEED, codec.DESCRIBE},
    } == CALLER_METHODS


async def test_an_import_streams_its_bundle_and_installs_it_as_the_call_s_actor(
    api: SignerClient, units: Store, holder: Issued
) -> None:
    data = bundle(holder, password=PASSWORD)
    outcome = await api.import_local(CALL, REQUEST, Secret(PASSWORD), parts_of(data, 512))
    assert len(data) > 2 * 512  # several chunks
    assert (outcome.replayed, outcome.view.status, outcome.view.backend) == (
        False,
        CertificateStatus.ACTIVE,
        BackendKind.LOCAL,
    )
    assert outcome.view.fingerprint_sha256 == holder.certificate.fingerprint(hashes.SHA256())
    assert [(audit.action, audit.actor) for audit in units.audits] == [("certificate.install", ACTOR)]
    replayed = await api.import_local(CALL, REQUEST, Secret(PASSWORD), parts_of(data))
    assert (replayed.replayed, replayed.view) == (True, outcome.view)


async def test_a_remote_import_is_its_header_alone(api: SignerClient, units: Store) -> None:
    outcome = await api.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    assert (outcome.view.backend, outcome.view.modes) == (BackendKind.REMOTE, frozenset({Mode.TEST}))
    assert [audit.actor for audit in units.audits] == [ACTOR]


@pytest.mark.parametrize(
    "requests",
    [
        (),
        (chunk(),),
        (local(), local()),
        (local(), chunk(), local()),
        (local(), signer_pb2.ImportCertificateRequest()),
    ],
    ids=["nothing", "a chunk first", "the header twice", "a header among chunks", "an empty part"],
)
async def test_an_import_s_header_comes_first_and_once(
    stub: SignerServiceAsyncStub, units: Store, requests: tuple[signer_pb2.ImportCertificateRequest, ...]
) -> None:
    error = await refused_import(stub, *requests)
    assert (error.status, error.remote_code) == (
        grpc.StatusCode.INVALID_ARGUMENT,
        ImportHeaderFirstError.code,
    )
    assert (units.rows, units.audits) == ({}, [])


async def test_a_remote_import_with_chunks_is_refused(stub: SignerServiceAsyncStub, units: Store) -> None:
    error = await refused_import(stub, remote(), chunk())
    assert error.remote_code == ImportChunksUnexpectedError.code
    assert units.rows == {}


async def test_a_bundle_over_its_limit_is_refused_and_nothing_installed(
    stub: SignerServiceAsyncStub, units: Store
) -> None:
    whole = MAX_BUNDLE_BYTES // CHUNK
    error = await refused_import(stub, local(), *(chunk(bytes(CHUNK)) for _ in range(whole)), chunk(b"\x00"))
    assert (error.status, error.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, ImportTooLargeError.code)
    assert units.rows == {}


def without_source() -> signer_pb2.ImportCertificateRequest:
    request = local()
    request.header.ClearField("local")
    return request


def without_actor() -> signer_pb2.ImportCertificateRequest:
    request = remote()
    request.header.call.actor = ""
    return request


def without_modes() -> signer_pb2.ImportCertificateRequest:
    request = remote()
    request.header.ClearField("modes")
    return request


@pytest.mark.parametrize(
    "unreadable", [without_source(), without_actor(), without_modes()], ids=["source", "actor", "modes"]
)
async def test_an_import_header_that_cannot_be_read_is_malformed(
    stub: SignerServiceAsyncStub, units: Store, unreadable: signer_pb2.ImportCertificateRequest
) -> None:
    error = await refused_import(stub, unreadable)
    assert (error.status, error.remote_code) == (grpc.StatusCode.INVALID_ARGUMENT, MalformedRequestError.code)
    assert units.rows == {}


async def test_certificates_are_described_and_retired_as_the_call_s_actor(
    api: SignerClient, units: Store, holder: Issued
) -> None:
    certificate = await installed(api, holder)
    [described] = await api.describe(CALL, IN_TEST, include_retired=False)
    retired = await api.retire(CALL, IN_TEST, described.id)
    assert described.id == retired.id == certificate
    assert (retired.status, retired.retired_reason) == (CertificateStatus.RETIRED, "retired")
    assert await api.describe(CALL, IN_TEST, include_retired=False) == ()
    assert [audit.actor for audit in units.audits] == [ACTOR, ACTOR]


async def test_a_document_and_a_seed_are_signed_and_audited_for_their_caller(
    api: SignerClient, worker: SignerClient, units: Store, holder: Issued, clock: Moving
) -> None:
    certificate = await installed(api, holder)
    signing = SystemCall(reason="ecf_emission", scope=IN_TEST, call_id="call-8")
    signed = await worker.sign_xml(signing, document(clock.now))
    seed = SeedToSign(tenant=IN_TEST, profile=PROFILE, seed=b"<s>1</s>", audience="ecf.dgii.gov.do")
    signed_seed = await worker.sign_seed(signing, seed)
    assert signed.certificate == signed_seed.certificate == certificate
    assert b"SignatureValue" in signed.document
    xml, seeded = units.audits[1:]
    assert (xml.action, xml.actor, xml.detail["caller"], xml.detail["reference"]) == (
        "certificate.sign_xml",
        Actor("system:ecf_emission", "call-8"),
        "worker",
        "E310000000001",
    )
    assert xml.detail["document_sha256"] == hashlib.sha256(DOCUMENT).hexdigest()
    assert (seeded.action, seeded.detail["caller"], seeded.detail["audience"]) == (
        "certificate.sign_seed",
        "worker",
        "ecf.dgii.gov.do",
    )


def unspecified_mode() -> signer_pb2.SignXmlRequest:
    request = codec.xml_request(CALL, document(NOW))
    request.mode = signer_pb2.MODE_UNSPECIFIED
    return request


def without_call_id() -> signer_pb2.SignXmlRequest:
    request = codec.xml_request(CALL, document(NOW))
    request.call.call_id = ""
    return request


@pytest.mark.parametrize("unreadable", [unspecified_mode(), without_call_id()], ids=["mode", "call id"])
async def test_a_signature_request_that_cannot_be_read_is_malformed(
    stub: SignerServiceAsyncStub, unreadable: signer_pb2.SignXmlRequest
) -> None:
    with pytest.raises(RemoteError) as caught:
        await call_unary(
            stub.SignXml, unreadable, rpc=codec.SIGN_XML, deadline=DEADLINE, metadata=sent("worker")
        )
    assert caught.value.remote_code == MalformedRequestError.code


async def test_each_method_refuses_a_request_without_its_fields(stub: SignerServiceAsyncStub) -> None:
    context = codec.call_context(CALL)
    describe = stub.DescribeCertificates
    retire = stub.RetireCertificate
    seed = stub.SignSeed
    answers = (
        call_unary(
            describe,
            signer_pb2.DescribeCertificatesRequest(call=context),
            rpc=codec.DESCRIBE,
            deadline=DEADLINE,
            metadata=sent("api"),
        ),
        call_unary(
            retire,
            signer_pb2.RetireCertificateRequest(call=context),
            rpc=codec.RETIRE,
            deadline=DEADLINE,
            metadata=sent("api"),
        ),
        call_unary(
            seed,
            signer_pb2.SignSeedRequest(call=context),
            rpc=codec.SIGN_SEED,
            deadline=DEADLINE,
            metadata=sent("worker"),
        ),
    )
    for answer in answers:
        with pytest.raises(RemoteError) as caught:
            await answer
        assert caught.value.remote_code == MalformedRequestError.code


async def test_a_company_without_a_certificate_cannot_sign(worker: SignerClient, clock: Moving) -> None:
    with pytest.raises(SigningCertificateMissingError):
        await worker.sign_xml(CALL, document(clock.now))


async def test_damaged_custody_is_a_defect_the_caller_is_told_nothing_of(
    api: SignerClient, worker: SignerClient, custody: Damageable, holder: Issued, clock: Moving
) -> None:
    _ = await installed(api, holder)
    custody.damaged = True
    with pytest.raises(SignerDefectError) as caught:
        await worker.sign_xml(CALL, document(clock.now))
    cause = caught.value.__cause__
    assert isinstance(cause, RemoteError)
    assert (cause.status, cause.remote_code) == (grpc.StatusCode.INTERNAL, INTERNAL_CODE)


async def test_the_api_signs_no_seed_and_the_worker_cannot_import(
    api: SignerClient, worker: SignerClient
) -> None:
    seed = SeedToSign(tenant=IN_TEST, profile=PROFILE, seed=b"<s>1</s>", audience="ecf.dgii.gov.do")
    with pytest.raises(RemoteDefectError) as signing:
        await api.sign_seed(CALL, seed)
    with pytest.raises(RemoteDefectError) as importing:
        await worker.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    assert signing.value.remote_code == importing.value.remote_code == MethodNotAllowedError.code


def of_root(root: str) -> bytes:
    return f"<{root}><Encabezado><Nombre>DOCUMENT-SENTINEL</Nombre></Encabezado></{root}>".encode()


def signing(root: str, at: datetime) -> XmlToSign:
    return XmlToSign(tenant=IN_TEST, profile=PROFILE, document=of_root(root), signed_at=at, reference="ref-1")


async def test_the_api_signs_an_e_cf_and_a_summary_and_nothing_else_which_the_worker_still_signs(
    api: SignerClient, worker: SignerClient, holder: Issued, clock: Moving
) -> None:
    """The api's ``SignXml`` is the synchronous path's alone (ADR 0010): an e-CF or an RFCE, never an
    approval, a void, a receipt or anything else; the worker and the receiver sign whatever they are asked."""
    await installed(api, holder)
    for root in ("ECF", "RFCE"):
        assert b"SignatureValue" in (await api.sign_xml(CALL, signing(root, clock.now))).document
    for root in ("ACECF", "ANECF", "ARECF", "SemillaModel", "Otro"):
        with pytest.raises(RemoteDefectError) as refused:
            await api.sign_xml(CALL, signing(root, clock.now))
        assert refused.value.remote_code == MethodNotAllowedError.code
        assert b"SignatureValue" in (await worker.sign_xml(CALL, signing(root, clock.now))).document


async def test_the_api_s_document_that_does_not_read_is_refused_before_anything_is_signed(
    api: SignerClient, holder: Issued, clock: Moving, units: Store
) -> None:
    await installed(api, holder)
    audited = len(units.audits)
    unreadable = XmlToSign(
        tenant=IN_TEST, profile=PROFILE, document=b"<ECF>", signed_at=clock.now, reference="r"
    )
    with pytest.raises(RemoteDefectError) as refused:
        await api.sign_xml(CALL, unreadable)
    assert refused.value.remote_code == MethodNotAllowedError.code
    assert len(units.audits) == audited


def spoken(span: ReadableSpan) -> str:
    """What a span says: its name, its attributes and its events' attributes."""
    events = [dict(event.attributes or {}) for event in span.events]
    return f"{span.name} {dict(span.attributes or {})} {events}"


async def test_secrets_and_documents_never_leave(
    served: Served, clients: Clients, units: Store, custody: Damageable, holder: Issued
) -> None:
    api, worker = clients.api, clients.worker
    live = ImportRequest(
        tenant=Tenant(ORGANIZATION, COMPANY, Mode.LIVE),
        modes=frozenset({Mode.LIVE}),
        idempotency_key="import-0002",
    )
    errors: list[BaseException] = []
    with capture_logs() as logs:
        with pytest.raises(BundleUnreadableError) as unreadable:
            await api.import_local(CALL, REQUEST, Secret(PASSWORD + "-wrong"), parts_of(b"not a bundle"))
        errors.append(unreadable.value)
        _ = await installed(api, holder)
        _ = await api.import_remote(CALL, live, Secret(CREDENTIAL))
        _ = await worker.sign_xml(CALL, document(NOW))
        custody.damaged = True
        with pytest.raises(SignerDefectError) as defect:
            await worker.sign_xml(CALL, document(NOW))
        errors.append(defect.value)
    spans = served.spans.get_finished_spans()
    seen = [
        repr(logs),
        *(f"{error} {error.__cause__!r}" for error in errors),
        *(spoken(span) for span in spans),
        *(repr(dict(audit.detail)) for audit in units.audits),
        *(repr(payload_of(event)) for event in units.events),
    ]
    assert len(spans) == 5
    assert [text for text in seen if any(secret in text for secret in SENTINELS)] == []


async def test_the_server_says_where_it_serves_only_while_it_serves(servicer: SignerServicer) -> None:
    server = SignerServer(
        servicer, port=0, registry=CollectorRegistry(), callers=callers(), options=ServerOptions(grace=0.1)
    )
    with pytest.raises(SignerNotServingError):
        await server.ready()
    async with server:
        await server.ready()
        assert server.port > 0
    with pytest.raises(SignerNotServingError):
        _ = server.port
    assert all(token not in repr(server) for token in TOKENS.values())
    await server.__aexit__(None, None, None)  # stopping a stopped server does nothing


async def held_open(
    *requests: signer_pb2.ImportCertificateRequest,
) -> AsyncGenerator[signer_pb2.ImportCertificateRequest]:
    """``requests``, then the stream held open until the call ends, sending nothing more."""
    for request in requests:
        yield request
    await asyncio.Event().wait()  # nobody sets it: the service gives up, and the call cancels this


@pytest.mark.parametrize("import_seconds", [HELD_IMPORT_SECONDS])
async def test_an_import_held_open_is_refused_once_the_service_stops_waiting(
    stub: SignerServiceAsyncStub, units: Store
) -> None:
    with pytest.raises(RemoteError) as caught:
        await call_stream_unary(
            stub.ImportCertificate,
            held_open(local()),
            rpc=codec.IMPORT,
            deadline=IMPORT_DEADLINE,
            metadata=sent("api"),
        )
    assert (caught.value.status, caught.value.remote_code) == (
        grpc.StatusCode.INVALID_ARGUMENT,
        ImportTooSlowError.code,
    )
    assert units.rows == {}


async def test_a_bundle_at_its_limit_is_read_and_one_byte_more_is_refused(
    stub: SignerServiceAsyncStub,
) -> None:
    whole = [chunk(bytes(CHUNK)) for _ in range(MAX_BUNDLE_BYTES // CHUNK)]
    at_limit = await refused_import(stub, local(), *whole)
    over = await refused_import(stub, local(), *whole, chunk(b"\0"))
    assert (at_limit.remote_code, over.remote_code) == (BundleUnreadableError.code, ImportTooLargeError.code)


@pytest.mark.parametrize("call_id", ["another-call", None], ids=["another", "none"])
async def test_a_call_naming_another_call_id_than_its_metadata_is_malformed(
    stub: SignerServiceAsyncStub, call_id: str | None
) -> None:
    metadata = sent("worker", call_id or "x")
    if call_id is None:
        metadata = tuple(item for item in metadata if item[0] != CALL_ID_KEY)
    with pytest.raises(RemoteError) as caught:
        await call_unary(
            stub.SignXml,
            codec.xml_request(CALL, document(NOW)),
            rpc=codec.SIGN_XML,
            deadline=DEADLINE,
            metadata=metadata,
        )
    assert caught.value.remote_code == MalformedRequestError.code
