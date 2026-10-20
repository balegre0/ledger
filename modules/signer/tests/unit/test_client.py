"""The signer's gRPC client against a stub ``SignerService`` served on port 0 behind the platform's guard.

The stub answers as a test tells it and records what each call carried: its request, its caller (as the
guard admitted it) and its metadata. Each signer error is raised by the stub as the signer raises it, so the
server's own mapping of kinds to statuses is what the client reads back; ``abort`` answers a status and a
code the signer would never pair, to show the client trusts neither alone.
"""

import asyncio
import dataclasses
import logging
import traceback
from datetime import UTC, datetime, timedelta
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator

import grpc
import pytest
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from pydantic import SecretStr

from ledger_fiscal_kit.ports import (
    CertificateValidity,
    SeedToSign,
    SignatureProfile,
    SignatureRefusedError,
    SignerUnavailableError,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_fiscal_kit.xml.dsig import DocumentSignedError, NotAnRsaKeyError, SigningPairError
from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    InvalidInputError,
    InvalidValueError,
    LedgerError,
    NotFoundError,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.data.idempotency import InvalidIdempotencyKeyError
from ledger_platform.grpc.client import (
    ERROR_CODE_KEY,
    RemoteConflictError,
    RemoteDefectError,
    RemoteError,
)
from ledger_platform.grpc.guard import CallerPolicy, CallerTokens, current_caller
from ledger_platform.grpc.metadata import AUTHORIZATION_KEY, CALL_ID_KEY, TRACEPARENT_KEY, metadata_value
from ledger_platform.grpc.server import ServerOptions, serve_grpc
from ledger_platform.http.idempotency import IdempotencyKeyInUseError, IdempotencyKeyReusedError
from ledger_platform.runtime.calls import SystemCall
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.client import (
    API_REFUSALS,
    CHANNEL_OPTIONS,
    CHUNK_BYTES,
    SignerClient,
    SignerDefectError,
    SignerTokenRefusedError,
)
from ledger_signer.adapters.grpc.codec import (
    ImportChunksUnexpectedError,
    ImportHeaderFirstError,
    ImportTooSlowError,
)
from ledger_signer.adapters.http.router import BodyIncompleteError
from ledger_signer.adapters.serving import SignerWorkUnreadableError
from ledger_signer.api import (
    BackendKind,
    BackendNotOfferedError,
    CertificateId,
    CertificateNotFoundError,
    CertificateStatus,
    CertificateView,
)
from ledger_signer.application.certificates import CertificateServesLiveError
from ledger_signer.application.facade import port_error
from ledger_signer.application.importing import (
    MAX_BUNDLE_BYTES,
    ImportModesRefusedError,
    ImportRequest,
    ImportTooLargeError,
)
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    BundleIncompleteError,
    BundleUnreadableError,
    EntityRefusedError,
    EntityUnavailableError,
    ImportOutcome,
    KmsUnavailableError,
    Secret,
)
from ledger_signer.application.signing import CustodyDamagedError
from ledger_signer.domain.certificates import MIN_RSA_BITS
from ledger_signer.domain.errors import (
    CertificateExpiredError,
    CertificateKeyMismatchError,
    CertificateKeyUnsupportedError,
    CertificateNotForSigningError,
    CertificateNotYetValidError,
    CertificateRetiredError,
    CertificateValidityTooShortError,
    ClockSkewError,
    NotCoveredError,
)

API_TOKEN: Final = "api-token-0123456789abcdefghijklmnopqrstuv"
WORKER_TOKEN: Final = "worker-token-0123456789abcdefghijklmnopq"
ORGANIZATION: Final = OrganizationId(new_id())
COMPANY: Final = CompanyId(new_id())
TENANT: Final = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
CALL: Final = SystemCall(reason="ecf_emission", scope=TENANT, call_id="call-1")
AT: Final = datetime(2026, 10, 19, 12, 30, tzinfo=UTC)
PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
DOCUMENT: Final = XmlToSign(
    tenant=TENANT, profile=PROFILE, document=b"<a/>", signed_at=AT, reference="E310000000001"
)
SEED: Final = SeedToSign(tenant=TENANT, profile=PROFILE, seed=b"<s/>", audience="ecf.dgii.gov.do")
SIGNED_BY: Final = CertificateId(new_id())
ACTIVE: Final = CertificateView(
    id=SIGNED_BY,
    organization=ORGANIZATION,
    company=COMPANY,
    modes=frozenset({Mode.TEST}),
    backend=BackendKind.LOCAL,
    status=CertificateStatus.ACTIVE,
    fingerprint_sha256=bytes(32),
    serial_number="0a",
    subject="CN=Juana Perez",
    issuer="CN=Avansi CA",
    not_before=AT - timedelta(days=1),
    not_after=AT + timedelta(days=364),
    installed_at=AT,
    retired_at=None,
    retired_reason=None,
    version=1,
)
RETIRED: Final = dataclasses.replace(
    ACTIVE,
    id=CertificateId(new_id()),
    status=CertificateStatus.RETIRED,
    retired_at=AT,
    retired_reason="replaced",
)
REQUEST: Final = ImportRequest(tenant=TENANT, modes=frozenset({Mode.TEST}), idempotency_key="import-0001")
PASSWORD: Final = "bundle-password-sentinel-7Hq2"
CREDENTIAL: Final = "entity-credential-sentinel-9Zx4"
DEADLINE: Final = 2.0

type Context[Request, Response] = grpc.aio.ServicerContext[Request, Response]


@final
@dataclasses.dataclass(slots=True)
class Received:
    """One call the stub took: which method, its request, who called and what its metadata said."""

    method: str
    request: object
    caller: str
    metadata: tuple[tuple[str, str | bytes], ...]


@final
class Stub(signer_pb2_grpc.SignerServiceServicer):
    """A signer that answers as told (``failure``, ``delay``, ``listed``) and records every call."""

    def __init__(self) -> None:
        self.received: list[Received] = []
        self.failure: Exception | None = None
        self.delay = 0.0
        self.listed: list[CertificateView] = [ACTIVE]
        self.chunks: list[bytes] = []
        self.abort: tuple[grpc.StatusCode, str] | None = None

    async def _took[Request, Response](
        self, method: str, request: object, context: Context[Request, Response]
    ) -> None:
        self.received.append(
            Received(method, request, current_caller(), tuple(context.invocation_metadata() or ()))
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.abort is not None:
            status, code = self.abort
            await context.abort(status, "refused", trailing_metadata=((ERROR_CODE_KEY, code),))
        if self.failure is not None:
            raise self.failure

    @override
    async def ImportCertificate(
        self,
        request_iterator: AsyncIterator[signer_pb2.ImportCertificateRequest],
        context: Context[signer_pb2.ImportCertificateRequest, signer_pb2.ImportCertificateResponse],
    ) -> signer_pb2.ImportCertificateResponse:
        parts = [part async for part in request_iterator]
        self.chunks = [part.chunk for part in parts[1:]]
        await self._took("import", parts[0].header, context)
        return codec.import_response(ImportOutcome(view=ACTIVE, replayed=bool(self.chunks)))

    @override
    async def DescribeCertificates(
        self,
        request: signer_pb2.DescribeCertificatesRequest,
        context: Context[signer_pb2.DescribeCertificatesRequest, signer_pb2.DescribeCertificatesResponse],
    ) -> signer_pb2.DescribeCertificatesResponse:
        await self._took("describe", request, context)
        return signer_pb2.DescribeCertificatesResponse(
            certificates=[codec.certificate_wire(view) for view in self.listed]
        )

    @override
    async def RetireCertificate(
        self,
        request: signer_pb2.RetireCertificateRequest,
        context: Context[signer_pb2.RetireCertificateRequest, signer_pb2.RetireCertificateResponse],
    ) -> signer_pb2.RetireCertificateResponse:
        await self._took("retire", request, context)
        return signer_pb2.RetireCertificateResponse(certificate=codec.certificate_wire(RETIRED))

    @override
    async def SignXml(
        self,
        request: signer_pb2.SignXmlRequest,
        context: Context[signer_pb2.SignXmlRequest, signer_pb2.SignXmlResponse],
    ) -> signer_pb2.SignXmlResponse:
        await self._took("sign_xml", request, context)
        return signer_pb2.SignXmlResponse(
            signed_document=request.document + b"<!--signed-->", certificate_id=str(SIGNED_BY)
        )

    @override
    async def SignSeed(
        self,
        request: signer_pb2.SignSeedRequest,
        context: Context[signer_pb2.SignSeedRequest, signer_pb2.SignSeedResponse],
    ) -> signer_pb2.SignSeedResponse:
        await self._took("sign_seed", request, context)
        return signer_pb2.SignSeedResponse(
            signed_seed=request.seed + b"<!--signed-->", certificate_id=str(SIGNED_BY)
        )


CALLERS: Final = CallerTokens(
    [
        CallerPolicy(
            name="api",
            tokens=(SecretStr(API_TOKEN),),
            methods=frozenset({codec.IMPORT, codec.DESCRIBE, codec.RETIRE}),
        ),
        CallerPolicy(
            name="worker",
            tokens=(SecretStr(WORKER_TOKEN),),
            methods=frozenset({codec.SIGN_XML, codec.SIGN_SEED, codec.DESCRIBE}),
        ),
    ]
)


@pytest.fixture
def stub() -> Stub:
    return Stub()


@pytest.fixture
async def address(stub: Stub) -> AsyncGenerator[str]:
    def register(server: grpc.aio.Server) -> None:
        signer_pb2_grpc.add_SignerServiceServicer_to_server(stub, server)

    async with serve_grpc(
        port=0,
        register=register,
        registry=CollectorRegistry(),
        callers=CALLERS,
        options=ServerOptions(grace=0.1),
    ) as port:
        yield f"127.0.0.1:{port}"


@pytest.fixture
async def worker(address: str) -> AsyncGenerator[SignerClient]:
    async with SignerClient(
        address, SecretStr(WORKER_TOKEN), deadline=DEADLINE, import_deadline=DEADLINE
    ) as client:
        yield client


@pytest.fixture
async def api(address: str) -> AsyncGenerator[SignerClient]:
    async with SignerClient(
        address, SecretStr(API_TOKEN), deadline=DEADLINE, import_deadline=DEADLINE
    ) as client:
        yield client


async def bundle(*parts: bytes) -> AsyncGenerator[bytes]:
    for part in parts:
        yield part


async def test_a_document_is_signed_with_the_caller_s_token_call_id_and_actor(
    worker: SignerClient, stub: Stub
) -> None:
    signed = await worker.sign_xml(CALL, DOCUMENT)
    assert (signed.document, signed.certificate) == (b"<a/><!--signed-->", SIGNED_BY)
    [received] = stub.received
    assert (received.method, received.caller) == ("sign_xml", "worker")
    assert metadata_value(received.metadata, AUTHORIZATION_KEY) == f"Bearer {WORKER_TOKEN}"
    assert metadata_value(received.metadata, CALL_ID_KEY) == "call-1"
    assert received.request == codec.xml_request(CALL, DOCUMENT)


async def test_a_seed_is_signed_with_its_audience(worker: SignerClient, stub: Stub) -> None:
    signed = await worker.sign_seed(CALL, SEED)
    assert (signed.document, signed.certificate) == (b"<s/><!--signed-->", SIGNED_BY)
    [received] = stub.received
    assert (received.method, received.request) == ("sign_seed", codec.seed_request(CALL, SEED))


async def test_the_trace_is_the_current_span_s_when_the_call_names_none(
    worker: SignerClient, stub: Stub
) -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("emit") as span:
        await worker.sign_xml(CALL, DOCUMENT)
    sent = metadata_value(stub.received[0].metadata, TRACEPARENT_KEY)
    assert sent is not None
    assert sent.split("-")[1] == format(span.get_span_context().trace_id, "032x")


async def test_the_trace_the_call_names_is_sent_alone(worker: SignerClient, stub: Stub) -> None:
    named = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    call = dataclasses.replace(CALL, traceparent=named)
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("emit"):
        await worker.sign_xml(call, DOCUMENT)
    await worker.sign_xml(call, DOCUMENT)
    for received in stub.received:
        assert [value for key, value in received.metadata if key == TRACEPARENT_KEY] == [named]


async def test_without_a_trace_none_is_sent(worker: SignerClient, stub: Stub) -> None:
    await worker.sign_xml(CALL, DOCUMENT)
    assert metadata_value(stub.received[0].metadata, TRACEPARENT_KEY) is None


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (NotCoveredError(), SigningCertificateNotValidError),
        (EntityRefusedError(), SigningCertificateNotValidError),
        (ClockSkewError(), SigningClockSkewError),
        (CertificateRetiredError(), SigningCertificateMissingError),
        (SigningCertificateMissingError(), SigningCertificateMissingError),
        (EntityUnavailableError(), SignerUnavailableError),
        (KmsUnavailableError(), SignerUnavailableError),
        (SignerUnavailableError(), SignerUnavailableError),
    ],
    ids=[
        "signer_window_not_covered",
        "entity_refused",
        "signer_clock_skew",
        "certificate_retired",
        "signing_certificate_missing",
        "entity_unavailable",
        "kms_unavailable",
        "signer_unavailable",
    ],
)
async def test_each_signer_refusal_is_the_port_s_error(
    worker: SignerClient, stub: Stub, failure: LedgerError, expected: type[LedgerError]
) -> None:
    stub.failure = failure
    with pytest.raises(expected) as caught:
        await worker.sign_xml(CALL, DOCUMENT)
    assert isinstance(caught.value.__cause__, RemoteError)
    assert type(port_error(failure.code, failure.kind)) is expected  # one mapping, in process and over gRPC


async def test_a_document_the_signer_cannot_sign_is_refused_with_its_reason(
    worker: SignerClient, stub: Stub
) -> None:
    stub.failure = DocumentSignedError()
    with pytest.raises(SignatureRefusedError) as caught:
        await worker.sign_xml(CALL, DOCUMENT)
    assert caught.value.reason == "xml_document_signed"


@pytest.mark.parametrize(
    "failure",
    [
        CustodyDamagedError(),
        RuntimeError("custody 4111111111111111"),
        NotAnRsaKeyError(),
        SigningPairError("x"),
    ],
    ids=["custody_damaged", "anything", "key_not_rsa", "pair_refused"],
)
async def test_a_defect_of_the_signer_is_a_defect_never_a_port_error(
    worker: SignerClient, stub: Stub, failure: Exception
) -> None:
    stub.failure = failure
    with pytest.raises(RemoteError) as caught:
        await worker.sign_xml(CALL, DOCUMENT)
    assert caught.value.kind is ErrorKind.INTERNAL
    assert "4111" not in str(caught.value)


async def test_a_signer_past_its_deadline_is_unavailable(address: str, stub: Stub) -> None:
    stub.delay = 1.0
    async with SignerClient(
        address, SecretStr(WORKER_TOKEN), deadline=0.2, import_deadline=DEADLINE
    ) as client:
        with pytest.raises(SignerUnavailableError):
            await client.sign_seed(CALL, SEED)


async def test_a_signer_that_is_not_there_is_unavailable() -> None:
    async with SignerClient(
        "127.0.0.1:1", SecretStr(WORKER_TOKEN), deadline=1.0, import_deadline=1.0
    ) as client:
        with pytest.raises(SignerUnavailableError):
            await client.sign_xml(CALL, DOCUMENT)


async def test_a_client_never_opened_or_closed_again_is_unavailable(address: str) -> None:
    client = SignerClient(address, SecretStr(WORKER_TOKEN), deadline=DEADLINE, import_deadline=DEADLINE)
    with pytest.raises(SignerUnavailableError):
        await client.sign_xml(CALL, DOCUMENT)
    async with client:
        pass
    await client.__aexit__(None, None, None)  # closing a closed client changes nothing
    with pytest.raises(SignerUnavailableError):
        await client.certificate_validity(CALL, TENANT)


async def test_the_validity_is_the_active_certificate_s(worker: SignerClient, stub: Stub) -> None:
    stub.listed = [RETIRED, ACTIVE]
    assert await worker.certificate_validity(CALL, TENANT) == CertificateValidity(
        certificate=ACTIVE.id, not_before=ACTIVE.not_before, not_after=ACTIVE.not_after
    )
    stub.listed = []
    assert await worker.certificate_validity(CALL, TENANT) is None
    [first, _] = stub.received
    assert first.request == codec.describe_request(CALL, TENANT, include_retired=False)


async def test_the_validity_of_a_tenant_without_a_mode_is_a_defect(worker: SignerClient) -> None:
    with pytest.raises(InvalidValueError):
        await worker.certificate_validity(CALL, Tenant(ORGANIZATION, COMPANY, None))


async def test_the_certificates_of_a_company_are_described_in_the_call_s_organization(
    worker: SignerClient, stub: Stub
) -> None:
    assert await worker.certificates(CALL, COMPANY, Mode.LIVE) == (ACTIVE,)
    assert stub.received[0].request == codec.describe_request(
        CALL, Tenant(ORGANIZATION, COMPANY, Mode.LIVE), include_retired=False
    )
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        await worker.certificates(SystemCall(reason="probe", scope=None), COMPANY, Mode.LIVE)


async def test_the_api_describes_with_retired_ones_and_retires(api: SignerClient, stub: Stub) -> None:
    stub.listed = [ACTIVE, RETIRED]
    assert await api.describe(CALL, TENANT, include_retired=True) == (ACTIVE, RETIRED)
    company = Tenant(ORGANIZATION, COMPANY, Mode.TEST)
    assert await api.retire(CALL, company, RETIRED.id) == RETIRED
    assert [received.caller for received in stub.received] == ["api", "api"]
    assert stub.received[1].request == codec.retire_request(CALL, company, RETIRED.id)


async def test_a_signer_that_cannot_reach_its_kms_is_unavailable_to_the_api(
    api: SignerClient, stub: Stub
) -> None:
    stub.failure = KmsUnavailableError()
    with pytest.raises(SignerUnavailableError):
        await api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(b"pkcs12"))
    stub.failure = CertificateRetiredError()
    with pytest.raises(RemoteConflictError):  # a signature's conflict outside a signature: no port error
        await api.describe(CALL, TENANT, include_retired=False)


async def test_a_bundle_streams_after_its_header_in_chunks_of_at_most_64_kib(
    api: SignerClient, stub: Stub
) -> None:
    whole = bytes(range(256)) * 600  # 150 KiB
    outcome = await api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(whole[:10], b"", whole[10:]))
    assert outcome == ImportOutcome(view=ACTIVE, replayed=True)  # the stub says "replayed" when chunks came
    assert b"".join(stub.chunks) == whole
    assert max(len(chunk) for chunk in stub.chunks) == CHUNK_BYTES == 64 * 1024
    assert all(stub.chunks)
    [received] = stub.received
    assert received.request == codec.local_header(CALL, REQUEST, Secret(PASSWORD))


async def test_a_remote_import_is_its_header_alone(api: SignerClient, stub: Stub) -> None:
    outcome = await api.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    assert outcome == ImportOutcome(view=ACTIVE, replayed=False)
    assert stub.chunks == []
    assert stub.received[0].request == codec.remote_header(CALL, REQUEST, Secret(CREDENTIAL))


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_an_empty_or_short_own_token_is_refused_when_the_client_is_built(token: str) -> None:
    with pytest.raises(SignerTokenRefusedError) as caught:
        SignerClient("signer:50051", SecretStr(token), deadline=DEADLINE, import_deadline=DEADLINE)
    assert str(caught.value) == "the signer client's own token must be at least 32 characters"


@pytest.mark.parametrize("deadline", [0.0, -1.0, 10.5])
def test_a_deadline_outside_its_bounds_is_refused(deadline: float) -> None:
    with pytest.raises(LedgerError, match="deadline"):
        SignerClient("signer:50051", SecretStr(WORKER_TOKEN), deadline=deadline, import_deadline=DEADLINE)
    with pytest.raises(LedgerError, match="import_deadline"):
        SignerClient("signer:50051", SecretStr(WORKER_TOKEN), deadline=DEADLINE, import_deadline=deadline * 3)


def test_the_client_never_shows_its_token() -> None:
    client = SignerClient("signer:50051", SecretStr(WORKER_TOKEN), deadline=DEADLINE, import_deadline=30.0)
    assert repr(client) == "SignerClient(address='signer:50051', deadline=2.0, import_deadline=30.0)"
    assert WORKER_TOKEN not in repr(client)


def test_the_channel_backs_off_between_half_a_second_and_five() -> None:
    assert dict(CHANNEL_OPTIONS) == {
        "grpc.max_send_message_length": 5 * 1024 * 1024,
        "grpc.max_receive_message_length": 5 * 1024 * 1024,
        "grpc.initial_reconnect_backoff_ms": 500,
        "grpc.min_reconnect_backoff_ms": 1000,
        "grpc.max_reconnect_backoff_ms": 5000,
    }


@pytest.mark.parametrize(
    "failure", [CustodyDamagedError(), RuntimeError("anything")], ids=["custody_damaged", "anything"]
)
async def test_a_defect_the_signer_reports_is_a_signer_defect(
    worker: SignerClient, api: SignerClient, stub: Stub, failure: Exception
) -> None:
    """The signer answers its own defects ``INTERNAL`` with the platform's ``internal`` code, and no more."""
    stub.failure = failure
    with pytest.raises(SignerDefectError) as signing:
        await worker.sign_seed(CALL, SEED)
    with pytest.raises(SignerDefectError) as retiring:
        await api.retire(CALL, TENANT, RETIRED.id)
    for caught in (signing, retiring):
        assert (caught.value.code, caught.value.kind, caught.value.remote_code) == (
            "signer_defect",
            ErrorKind.INTERNAL,
            "internal",
        )


async def test_a_transport_s_internal_failure_is_the_signer_unavailable(
    worker: SignerClient, stub: Stub
) -> None:
    stub.abort = (grpc.StatusCode.INTERNAL, "unknown")
    with pytest.raises(SignerUnavailableError):
        await worker.sign_xml(CALL, DOCUMENT)


async def test_the_guard_s_refusals_are_defects_of_the_deployment(
    address: str, api: SignerClient, worker: SignerClient
) -> None:
    """Never forbidden (a 403 would blame the route's caller): a token or an allowlist is wrong."""
    with pytest.raises(RemoteDefectError) as refused:
        await api.sign_xml(CALL, DOCUMENT)
    with pytest.raises(RemoteDefectError) as importing:
        await worker.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    async with SignerClient(
        address, SecretStr("x" * 40), deadline=DEADLINE, import_deadline=DEADLINE
    ) as stranger:
        with pytest.raises(RemoteDefectError) as unknown:
            await stranger.describe(CALL, TENANT, include_retired=False)
    caught = (refused, importing, unknown)
    assert [error.value.remote_code for error in caught] == [
        "grpc_method_not_allowed",
        "grpc_method_not_allowed",
        "grpc_caller_unknown",
    ]
    assert all(error.value.kind is ErrorKind.INTERNAL for error in caught)


IMPORT_REFUSALS: Final[tuple[LedgerError, ...]] = (
    CertificateKeyUnsupportedError(MIN_RSA_BITS),
    CertificateNotForSigningError(),
    CertificateKeyMismatchError(),
    CertificateExpiredError(),
    CertificateNotYetValidError(),
    CertificateValidityTooShortError(),
    BundleUnreadableError(),
    BundleIncompleteError(),
    BackendNotOfferedError(BackendKind.REMOTE),
    ImportTooLargeError(),
    ImportHeaderFirstError(),
    ImportChunksUnexpectedError(),
    ImportTooSlowError(),
    InvalidIdempotencyKeyError(),
    IdempotencyKeyReusedError(),
    IdempotencyKeyInUseError(),
    EntityRefusedError(),
    ActiveCertificateTakenError(),
    ImportModesRefusedError(),
)


@pytest.mark.parametrize("original", IMPORT_REFUSALS, ids=[error.code for error in IMPORT_REFUSALS])
async def test_an_import_s_refusal_is_the_error_the_facade_raises(
    api: SignerClient, stub: Stub, original: LedgerError
) -> None:
    """What the HTTP edge maps to its problems, whichever process answered (Task 13)."""
    stub.failure = original
    with pytest.raises(LedgerError) as caught:
        await api.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    raised = caught.value
    assert type(raised) is type(original)
    assert (raised.code, raised.kind, str(raised)) == (original.code, original.kind, str(original))
    cause = raised.__cause__
    assert isinstance(cause, RemoteError)
    assert cause.remote_code == original.code


async def test_a_certificate_the_company_does_not_have_is_not_found(api: SignerClient, stub: Stub) -> None:
    stub.failure = CertificateNotFoundError()
    with pytest.raises(CertificateNotFoundError) as caught:
        await api.retire(CALL, TENANT, RETIRED.id)
    assert isinstance(caught.value.__cause__, RemoteError)
    stub.failure = CertificateServesLiveError()
    with pytest.raises(CertificateServesLiveError) as serving:
        await api.retire(CALL, TENANT, ACTIVE.id)
    assert isinstance(serving.value.__cause__, RemoteError)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (grpc.StatusCode.FAILED_PRECONDITION, "certificate_expired", RemoteConflictError),
        (grpc.StatusCode.NOT_FOUND, "certificate_expired", RemoteError),
        (grpc.StatusCode.INVALID_ARGUMENT, "certificate_not_found", RemoteDefectError),
        (grpc.StatusCode.INVALID_ARGUMENT, "a_code_no_signer_sends", RemoteDefectError),
        (grpc.StatusCode.FAILED_PRECONDITION, "a_code_no_signer_sends", RemoteConflictError),
    ],
    ids=[
        "invalid_as_conflict",
        "invalid_as_not_found",
        "not_found_as_invalid",
        "unknown",
        "unknown_conflict",
    ],
)
async def test_a_refusal_whose_status_does_not_fit_its_code_stays_a_remote_error(
    api: SignerClient, stub: Stub, status: grpc.StatusCode, code: str, expected: type[RemoteError]
) -> None:
    stub.abort = (status, code)
    with pytest.raises(RemoteError) as caught:
        await api.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
    assert isinstance(caught.value, expected)
    assert caught.value.remote_code == code


async def test_a_backend_refused_outside_an_import_stays_a_remote_error(
    api: SignerClient, stub: Stub
) -> None:
    """Only an import knows its backend: the signer never refuses one elsewhere, so it is no refusal there."""
    stub.failure = BackendNotOfferedError(BackendKind.LOCAL)
    with pytest.raises(RemoteDefectError) as caught:
        await api.describe(CALL, TENANT, include_retired=True)
    assert caught.value.remote_code == "backend_not_offered"
    stub.failure = BackendNotOfferedError(BackendKind.LOCAL)
    with pytest.raises(BackendNotOfferedError) as local:
        await api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(b"pkcs12"))
    assert str(local.value) == str(BackendNotOfferedError(BackendKind.LOCAL))


SIGNATURE_ONLY: Final = frozenset(
    {
        NotCoveredError.code,
        ClockSkewError.code,
        CertificateRetiredError.code,
    }
)
"""The signer's refusals no API call answers: a signature's, mapped to the port's errors by ``port_error``."""
UNANSWERED: Final = frozenset({SignerWorkUnreadableError.code, BodyIncompleteError.code})
"""The refusals no call of the signer answers: a job's or an event's it cannot read (they end it dead), and
the api's own refusal of a request whose body never ended (no call was made)."""


def declared(base: type[LedgerError]) -> Iterator[type[LedgerError]]:
    """Every subclass of ``base`` declared in ``ledger_signer``, however deep."""
    for subclass in base.__subclasses__():
        if subclass.__module__.startswith("ledger_signer."):
            yield subclass
        yield from declared(subclass)


def test_every_refusal_the_signer_declares_is_mapped_or_known_to_be_a_signature_s() -> None:
    """A new refusal of the signer fails here until the client builds it back (or it is a signature's)."""
    codes = {
        error.code for base in (InvalidInputError, ConflictError, NotFoundError) for error in declared(base)
    }
    assert codes - SIGNATURE_ONLY - UNANSWERED - frozenset(API_REFUSALS) == set()
    assert SIGNATURE_ONLY.isdisjoint(API_REFUSALS)
    answered = {CertificateNotFoundError.code, CertificateServesLiveError.code}
    assert {error.code for error in IMPORT_REFUSALS} | answered == set(API_REFUSALS)


async def test_an_import_waits_its_own_deadline(address: str, stub: Stub) -> None:
    stub.delay = 0.5
    async with SignerClient(address, SecretStr(API_TOKEN), deadline=0.2, import_deadline=DEADLINE) as client:
        outcome = await client.import_remote(CALL, REQUEST, Secret(CREDENTIAL))
        with pytest.raises(SignerUnavailableError):
            await client.describe(CALL, TENANT, include_retired=False)
    assert outcome.view == ACTIVE


class BundleSourceError(Exception):
    """What reading a bundle from the HTTP request raised: never grpc's to swallow."""


async def broken(*parts: bytes) -> AsyncGenerator[bytes]:
    for part in parts:
        yield part
    raise BundleSourceError


async def stalled(first: bytes) -> AsyncGenerator[bytes]:
    """A bundle whose first part comes, then nothing more: a client that stopped sending."""
    yield first
    await asyncio.Event().wait()


async def test_a_bundle_still_unsent_when_the_import_s_deadline_passes_is_too_slow(address: str) -> None:
    """Not ``signer_unavailable``: the signer waited, the body did not come (408 at the edge, Task 13)."""
    async with SignerClient(address, SecretStr(API_TOKEN), deadline=DEADLINE, import_deadline=0.3) as client:
        with pytest.raises(ImportTooSlowError) as caught:
            await client.import_local(CALL, REQUEST, Secret(PASSWORD), stalled(b"pkcs12"))
    cause = caught.value.__cause__
    assert isinstance(cause, RemoteError)
    assert cause.status is grpc.StatusCode.DEADLINE_EXCEEDED


async def test_a_whole_bundle_the_signer_did_not_answer_in_time_is_the_signer_unavailable(
    address: str, stub: Stub
) -> None:
    stub.delay = 0.5
    async with SignerClient(address, SecretStr(API_TOKEN), deadline=DEADLINE, import_deadline=0.2) as client:
        with pytest.raises(SignerUnavailableError):
            await client.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(b"pkcs12"))


async def test_a_bundle_that_cannot_be_read_raises_its_own_error_and_installs_nothing(
    api: SignerClient, stub: Stub
) -> None:
    with pytest.raises(BundleSourceError):
        await api.import_local(CALL, REQUEST, Secret(PASSWORD), broken(b"pkcs12", b"more"))
    assert stub.received == []


async def test_a_bundle_over_two_mib_is_refused_before_it_ends_and_installs_nothing(
    api: SignerClient, stub: Stub
) -> None:
    whole = bytes(MAX_BUNDLE_BYTES)
    with pytest.raises(ImportTooLargeError) as caught:
        await api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(whole, b"x"))
    assert caught.value.code == "signer_import_too_large"
    assert stub.received == []
    outcome = await api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(whole[:10], whole[10:]))
    assert outcome.view == ACTIVE
    assert sum(len(chunk) for chunk in stub.chunks) == MAX_BUNDLE_BYTES == 2 * 1024 * 1024


async def test_a_password_or_a_credential_never_leaves_in_an_error_or_a_log(
    api: SignerClient,
    stub: Stub,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    failures: list[BaseException] = []

    stub.failure = BundleUnreadableError()
    attempts: tuple[Callable[[], Awaitable[ImportOutcome]], ...] = (
        lambda: api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(b"pkcs12")),
        lambda: api.import_remote(CALL, REQUEST, Secret(CREDENTIAL)),
        lambda: api.import_local(CALL, REQUEST, Secret(PASSWORD), broken(b"pkcs12")),
        lambda: api.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(bytes(MAX_BUNDLE_BYTES + 1))),
    )
    for importing in attempts:
        with pytest.raises((BundleUnreadableError, BundleSourceError, ImportTooLargeError)) as caught:
            await importing()
        failures.append(caught.value)
    async with SignerClient("127.0.0.1:1", SecretStr(API_TOKEN), deadline=0.5, import_deadline=0.5) as gone:
        with pytest.raises(SignerUnavailableError) as unavailable:
            await gone.import_local(CALL, REQUEST, Secret(PASSWORD), bundle(b"pkcs12"))
        failures.append(unavailable.value)
    captured = capsys.readouterr()
    seen = [captured.out, captured.err, caplog.text]
    for error in failures:
        seen += [str(error), repr(error), repr(error.__cause__), "".join(traceback.format_exception(error))]
    for secret in (PASSWORD, CREDENTIAL):
        assert all(secret not in text for text in seen)
