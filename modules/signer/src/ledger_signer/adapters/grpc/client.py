"""``SignerPort`` and ``SignerApi`` over gRPC: what every app but the signer binds (plan Decisions 2, 12, 13).

``SignerClient`` is a resource: the app opens its channel at start and closes it at stop (calls in flight get
``deadline`` seconds to end); a call made while it is not open answers ``SignerUnavailableError``, as a signer
that cannot be reached does. It holds one caller's token (its role's, ``LEDGER_SIGNER_TOKEN_<ROLE>``), refused
when it is built if it is shorter than the guard takes (``MIN_TOKEN_LENGTH``), and never shows it. Every call
carries ``call_metadata`` (the token, the call id), the trace the call names (``Call.traceparent``) or else
the current span's, and the caller's actor and call id in its ``CallContext`` for the signer's audit. A call
waits ``deadline`` seconds (at most 10, spec §3.7), an import ``import_deadline`` (at most 30: its bundle
streams). Messages are at most 5 MiB both ways; a bundle streams after its header in chunks of at most
``CHUNK_BYTES``, refused (``ImportTooLargeError``) as soon as it passes ``MAX_BUNDLE_BYTES``, and the header,
which holds the password or the credential, is made only as the stream starts. A channel that lost the signer
tries again after half a second, then backs off up to five.

What the signer answers becomes what the signer's own process would raise:

- a signature: the port's five errors only, by ``application.facade.port_error`` (the one mapping of the
  signer's codes for both): ``signer_window_not_covered`` and ``entity_refused`` are
  ``SigningCertificateNotValidError``, ``signer_clock_skew`` ``SigningClockSkewError``,
  ``certificate_retired`` and ``signing_certificate_missing`` ``SigningCertificateMissingError``, a
  document's ``xml_*`` or ``signature_*`` refusal ``SignatureRefusedError`` with that code as its reason;
- any other call: a refusal of ``API_REFUSALS`` is built again as the facade raises it (the same type, code,
  kind and message), when its status is the one its kind is answered with; the HTTP edge maps those to its
  problems, whichever process answered;
- every call: whatever the signer could not reach (its KMS, the entity), a signer that is down, too slow or
  not open is ``SignerUnavailableError``; a defect the signer reports (``INTERNAL`` with the platform's
  ``INTERNAL_CODE``: custody damaged or tampered with, a KMS that refuses, an entity's answer that cannot be
  read) is ``SignerDefectError``; the guard's refusals (``grpc_caller_unknown``, ``grpc_method_not_allowed``:
  a token or an allowlist of the deployment is wrong) are ``RemoteDefectError``, never a 403 blaming the
  route's caller;
- anything else stays the platform's ``RemoteError`` family, the signer's code as ``remote_code``.

Each error built from an answer is raised from the ``RemoteError`` it came as, which names the call and keeps
the signer's code for the log. No readiness check calls the signer through this client: no app's readiness
depends on it (spec §3.7).
"""

from functools import partial
from typing import ClassVar, Final, Self, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Mapping, Sequence
lazy from types import TracebackType

import grpc
lazy from pydantic import SecretStr

from ledger_fiscal_kit.ports import SignerPort, SignerUnavailableError
from ledger_kernel.errors import ErrorKind, InvalidValueError, LedgerError
from ledger_kernel.tenancy import Tenant
from ledger_platform.config.options import require_seconds
from ledger_platform.data.idempotency import InvalidIdempotencyKeyError
from ledger_platform.grpc.client import (
    INTERNAL_CODE,
    MAX_DEADLINE,
    RemoteDefectError,
    RemoteError,
    RemoteForbiddenError,
    call_stream_unary,
    call_unary,
)
from ledger_platform.grpc.guard import MIN_TOKEN_LENGTH
from ledger_platform.grpc.metadata import TRACEPARENT_KEY, call_metadata
from ledger_platform.grpc.server import DEFAULT_MAX_MESSAGE_BYTES
from ledger_platform.http.idempotency import IdempotencyKeyInUseError, IdempotencyKeyReusedError
from ledger_platform.runtime.calls import actor_of
from ledger_platform.runtime.facade import facade
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.server import ImportChunksUnexpectedError, ImportHeaderFirstError
from ledger_signer.adapters.settings import MAX_DEADLINE_SECONDS
from ledger_signer.api import BackendKind, BackendNotOfferedError, CertificateNotFoundError, SignerApi
from ledger_signer.application.facade import port_error
from ledger_signer.application.importing import MAX_BUNDLE_BYTES, ImportTooLargeError
from ledger_signer.application.ports import (
    ActiveCertificateTakenError,
    BundleIncompleteError,
    BundleUnreadableError,
    EntityRefusedError,
)
from ledger_signer.domain.certificates import MIN_RSA_BITS, CertificateStatus
from ledger_signer.domain.errors import (
    CertificateExpiredError,
    CertificateKeyMismatchError,
    CertificateKeyUnsupportedError,
    CertificateNotForSigningError,
    CertificateNotYetValidError,
    CertificateValidityTooShortError,
)
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import Call, SystemCall
lazy from ledger_signer.adapters.grpc._generated.v1.signer_pb2_grpc import SignerServiceAsyncStub
lazy from ledger_signer.api import CertificateId, CertificateView
lazy from ledger_signer.application.importing import ImportRequest
lazy from ledger_signer.application.ports import ImportOutcome, Secret

CHUNK_BYTES: Final = 64 * 1024
"""The most of a bundle one message of an import carries (the contract's limit)."""
CHANNEL_OPTIONS: Final = (
    ("grpc.max_send_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.initial_reconnect_backoff_ms", 500),
    ("grpc.min_reconnect_backoff_ms", 1000),
    ("grpc.max_reconnect_backoff_ms", 5000),
)
"""5 MiB messages both ways; a lost signer tried again after 0.5 s, then every 1 s to 5 s. No keepalive: if
one is added, the signer's server must allow its pings (``grpc.http2.min_ping_interval_without_data_ms``)."""
_COMPONENT: Final = "signer client"
_STATUS_OF_KIND: Final[Mapping[ErrorKind, grpc.StatusCode]] = {
    ErrorKind.INVALID: grpc.StatusCode.INVALID_ARGUMENT,
    ErrorKind.CONFLICT: grpc.StatusCode.FAILED_PRECONDITION,
    ErrorKind.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
}
"""The status a chassis server answers each refusal's kind with (``grpc.server``)."""

type Metadata = tuple[tuple[str, str], ...]
type Refusal = Callable[[BackendKind | None], LedgerError | None]
"""Builds a refusal again, for the backend of the import that was refused (``None`` outside an import)."""


class SignerTokenRefusedError(LedgerError):
    """The client's own token is shorter than the signer's guard takes: a defect of the deployment."""

    code: ClassVar[str] = "signer_token_refused"

    def __init__(self) -> None:
        super().__init__(f"the signer client's own token must be at least {MIN_TOKEN_LENGTH} characters")


class SignerDefectError(RemoteError):
    """The signer failed on a defect of its own (custody, its KMS, an entity's answer): it says no more."""

    code: ClassVar[str] = "signer_defect"


def _always(make: Callable[[], LedgerError]) -> Refusal:
    """A refusal whatever the backend: ``make``'s error."""

    def refusal(backend: BackendKind | None) -> LedgerError:
        del backend
        return make()

    return refusal


def _backend_not_offered(backend: BackendKind | None) -> LedgerError | None:
    """The backend the import asked for is not offered; outside an import, no refusal the signer makes."""
    return None if backend is None else BackendNotOfferedError(backend)


API_REFUSALS: Final[Mapping[str, Refusal]] = {
    CertificateKeyUnsupportedError.code: _always(partial(CertificateKeyUnsupportedError, MIN_RSA_BITS)),
    BackendNotOfferedError.code: _backend_not_offered,
    **{
        refused.code: _always(refused)
        for refused in (
            CertificateNotForSigningError,
            CertificateKeyMismatchError,
            CertificateExpiredError,
            CertificateNotYetValidError,
            CertificateValidityTooShortError,
            BundleUnreadableError,
            BundleIncompleteError,
            ImportTooLargeError,
            ImportHeaderFirstError,
            ImportChunksUnexpectedError,
            InvalidIdempotencyKeyError,
            IdempotencyKeyReusedError,
            IdempotencyKeyInUseError,
            EntityRefusedError,
            ActiveCertificateTakenError,
            CertificateNotFoundError,
        )
    },
}
"""The refusals an API call of the signer answers (imports, descriptions, retirements), by code: each built
again as the signer's facade raises it. A refusal the signer declares is here, or is a signature's."""


def _common(error: RemoteError) -> LedgerError | None:
    """What ``error`` is for any call: the signer's defect, a deployment's, or the signer unavailable.

    ``None``: none of them. A transport's ``INTERNAL`` (no code of the chassis beside it) is the signer
    unavailable, as the platform reads it.
    """
    if error.status is grpc.StatusCode.INTERNAL and error.remote_code == INTERNAL_CODE:
        return SignerDefectError(error.rpc, error.status, error.raw_code)
    if isinstance(error, RemoteForbiddenError):
        return RemoteDefectError(error.rpc, error.status, error.raw_code)
    if error.kind is ErrorKind.UNAVAILABLE:
        return SignerUnavailableError()
    return None


def _signature_error(error: RemoteError) -> LedgerError | None:
    """The error a signature's ``error`` is (see the module); ``None``: ``error`` itself.

    The signer answers its ``INVALID`` errors ``INVALID_ARGUMENT``, which the platform reads as a defect of
    ours: for the port's mapping, that status is the signer's ``INVALID`` again.
    """
    kind = ErrorKind.INVALID if error.status is grpc.StatusCode.INVALID_ARGUMENT else error.kind
    return _common(error) or port_error(error.remote_code, kind)


def _api_error(error: RemoteError, backend: BackendKind | None) -> LedgerError | None:
    """The error any other call's ``error`` is (see the module); ``None``: ``error`` itself."""
    refusal = API_REFUSALS.get(error.remote_code)
    refused = None if refusal is None else refusal(backend)
    if refused is not None and _STATUS_OF_KIND.get(refused.kind) is error.status:
        return refused
    return _common(error)


async def _mapped[R](answer: Awaitable[R], mapping: Callable[[RemoteError], LedgerError | None]) -> R:
    """``answer``; a ``RemoteError`` it raises becomes what ``mapping`` says, or stays itself on ``None``."""
    try:
        return await answer
    except RemoteError as error:
        answered = mapping(error)
        if answered is None:
            raise
        raise answered from error


def _asked(error: RemoteError) -> LedgerError | None:
    """``_api_error`` outside an import."""
    return _api_error(error, None)


@final
class SignerClient(SignerPort, SignerApi):
    """The signer, reached over gRPC with one caller's token (see the module)."""

    __slots__ = ("_address", "_channel", "_deadline", "_import_deadline", "_stub", "_token")

    def __init__(self, address: str, token: SecretStr, *, deadline: float, import_deadline: float) -> None:
        """A client of the signer at ``address`` (``host:port``), not yet open.

        ``SignerTokenRefusedError`` for a token shorter than ``MIN_TOKEN_LENGTH``; ``InvalidOptionError`` for
        a ``deadline`` that is not a positive number of seconds up to ``MAX_DEADLINE_SECONDS``, or an
        ``import_deadline`` up to the platform's ``MAX_DEADLINE``.
        """
        if len(token.get_secret_value()) < MIN_TOKEN_LENGTH:
            raise SignerTokenRefusedError
        require_seconds(_COMPONENT, "deadline", deadline, ceiling=MAX_DEADLINE_SECONDS)
        require_seconds(_COMPONENT, "import_deadline", import_deadline, ceiling=MAX_DEADLINE)
        self._address = address
        self._token = token
        self._deadline = deadline
        self._import_deadline = import_deadline
        self._channel: grpc.aio.Channel | None = None
        self._stub: SignerServiceAsyncStub | None = None

    @override
    def __repr__(self) -> str:
        return (
            f"SignerClient(address={self._address!r}, deadline={self._deadline!r},"
            f" import_deadline={self._import_deadline!r})"
        )

    async def __aenter__(self) -> Self:
        """Open the channel; nothing is sent until the first call (grpc connects then)."""
        self._channel = grpc.aio.insecure_channel(self._address, options=CHANNEL_OPTIONS)
        self._stub = signer_pb2_grpc.SignerServiceStub(self._channel)
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the channel: calls in flight get ``deadline`` seconds, later ones are unavailable."""
        channel, self._channel, self._stub = self._channel, None, None
        if channel is not None:
            await channel.close(grace=self._deadline)

    def _opened(self) -> SignerServiceAsyncStub:
        if self._stub is None:
            raise SignerUnavailableError
        return self._stub

    def _metadata(self, call: Call | SystemCall) -> Metadata:
        """The token, the call id and the trace: the call's own, else the current span's if any."""
        sent = call_metadata(self._token, call_id=actor_of(call).call_id)
        traceparent = call.traceparent
        if traceparent is None:
            return sent
        return (*(item for item in sent if item[0] != TRACEPARENT_KEY), (TRACEPARENT_KEY, traceparent))

    def _unary[Request, Response](
        self,
        method: grpc.aio.UnaryUnaryMultiCallable[Request, Response],
        request: Request,
        call: Call | SystemCall,
        rpc: str,
    ) -> Awaitable[Response]:
        return call_unary(method, request, rpc=rpc, deadline=self._deadline, metadata=self._metadata(call))

    async def _import(
        self,
        call: Call | SystemCall,
        backend: BackendKind,
        header_of: Callable[[], signer_pb2.ImportHeader],
        bundle: AsyncIterable[bytes] | None,
    ) -> ImportOutcome:
        """Stream the header ``header_of`` makes, then ``bundle`` (if any) in chunks, as one import."""
        stub = self._opened()

        async def requests() -> AsyncGenerator[signer_pb2.ImportCertificateRequest]:
            yield signer_pb2.ImportCertificateRequest(header=header_of())
            if bundle is None:
                return
            size = 0
            async for part in bundle:
                size += len(part)
                if size > MAX_BUNDLE_BYTES:
                    raise ImportTooLargeError
                view = memoryview(part)
                for start in range(0, len(view), CHUNK_BYTES):
                    yield signer_pb2.ImportCertificateRequest(chunk=bytes(view[start : start + CHUNK_BYTES]))

        def refused(error: RemoteError) -> LedgerError | None:
            return _api_error(error, backend)

        sent = call_stream_unary(
            stub.ImportCertificate,
            requests(),
            rpc=codec.IMPORT,
            deadline=self._import_deadline,
            metadata=self._metadata(call),
        )
        return codec.import_outcome(await _mapped(sent, refused))

    @override
    @facade
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        stub = self._opened()
        sent = self._unary(stub.SignXml, codec.xml_request(call, request), call, codec.SIGN_XML)
        answer = await _mapped(sent, _signature_error)
        return codec.signed_of(answer.signed_document, answer.certificate_id)

    @override
    @facade
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        stub = self._opened()
        sent = self._unary(stub.SignSeed, codec.seed_request(call, request), call, codec.SIGN_SEED)
        answer = await _mapped(sent, _signature_error)
        return codec.signed_of(answer.signed_seed, answer.certificate_id)

    @override
    @facade
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        """The validity of the active certificate serving ``tenant`` (in a mode), described by the signer."""
        for view in await self.describe(call, tenant, include_retired=False):
            if view.status is CertificateStatus.ACTIVE:
                return CertificateValidity(
                    certificate=view.id, not_before=view.not_before, not_after=view.not_after
                )
        return None

    @override
    @facade
    async def certificates(
        self, call: Call | SystemCall, company: CompanyId, mode: Mode
    ) -> Sequence[CertificateView]:
        scope = call.scope
        if scope is None:
            raise InvalidValueError(field="call.scope", requirement="an organization or company scope")
        return await self.describe(call, Tenant(scope.organization, company, mode), include_retired=False)

    @facade
    async def describe(
        self, call: Call | SystemCall, tenant: Tenant, *, include_retired: bool
    ) -> tuple[CertificateView, ...]:
        """``tenant``'s certificates in its mode, active first; retired ones too if ``include_retired``."""
        request = codec.describe_request(call, tenant, include_retired=include_retired)
        sent = self._unary(self._opened().DescribeCertificates, request, call, codec.DESCRIBE)
        answer = await _mapped(sent, _asked)
        return tuple(codec.certificate_view(certificate) for certificate in answer.certificates)

    @facade
    async def retire(
        self, call: Call | SystemCall, company: Tenant, certificate: CertificateId
    ) -> CertificateView:
        """``certificate`` of ``company`` retired for every mode, or as it is if it was already."""
        request = codec.retire_request(call, company, certificate)
        sent = self._unary(self._opened().RetireCertificate, request, call, codec.RETIRE)
        answer = await _mapped(sent, _asked)
        return codec.certificate_view(answer.certificate)

    @facade
    async def import_local(
        self, call: Call | SystemCall, request: ImportRequest, password: Secret, bundle: AsyncIterable[bytes]
    ) -> ImportOutcome:
        """Install the certificate of the PKCS#12 ``bundle`` (streamed) opened with ``password``."""
        return await self._import(
            call, BackendKind.LOCAL, partial(codec.local_header, call, request, password), bundle
        )

    @facade
    async def import_remote(
        self, call: Call | SystemCall, request: ImportRequest, credential: Secret
    ) -> ImportOutcome:
        """Install the certificate an entity signs with for ``credential``: its header alone."""
        return await self._import(
            call, BackendKind.REMOTE, partial(codec.remote_header, call, request, credential), None
        )
