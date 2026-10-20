"""``SignerPort`` and ``SignerApi`` over gRPC: what every app but the signer binds (plan Decisions 2, 12, 13).

``SignerClient`` is a resource: the app opens its channel at start and closes it at stop; a call made while it
is not open answers ``SignerUnavailableError``, as a signer that cannot be reached does. It holds one caller's
token (its role's, ``LEDGER_SIGNER_TOKEN_<ROLE>``), refused when it is built if it is shorter than the guard
takes (``MIN_TOKEN_LENGTH``), and never shows it. Every call carries ``call_metadata`` (the token, the call
id), the trace the call names (``Call.traceparent``) or else the current span's, and the caller's actor and
call id in its ``CallContext`` for the signer's audit; every call has the client's deadline (at most 10 s,
spec §3.7). Messages are at most 5 MiB both ways (a document is at most 4 MiB); a bundle streams after its
header in chunks of at most ``CHUNK_BYTES``.

What the signer answers becomes, as the in-process facade's would (``application.facade.port_error``, one
mapping of the signer's codes for both):

- for a signature: the port's five errors only. ``signer_window_not_covered`` and ``entity_refused`` are
  ``SigningCertificateNotValidError``, ``signer_clock_skew`` ``SigningClockSkewError``,
  ``certificate_retired`` and ``signing_certificate_missing`` ``SigningCertificateMissingError``, a document's
  ``xml_*`` or ``signature_*`` refusal ``SignatureRefusedError`` with that code as its reason;
- for every call: whatever the signer could not reach (its KMS, the entity), a signer that is down, too slow
  or not open, is ``SignerUnavailableError``; a defect of the signer (it answered ``INTERNAL``: custody
  damaged or tampered with, a KMS that refuses, an entity's answer that cannot be read) is a
  ``RemoteDefectError``, never a port error nor the signer unavailable; ``certificate_not_found`` is
  ``CertificateNotFoundError``;
- anything else stays the platform's ``RemoteError`` family: the guard's refusals (``grpc_caller_unknown``,
  ``grpc_method_not_allowed``, ``RemoteForbiddenError``: a deployment's defect), a request the signer cannot
  read (``RemoteDefectError``), an import's refusals (``certificate_expired``, ``idempotency_key_reused``...)
  with the signer's code as ``remote_code``.

No readiness check calls the signer through this client: no app's readiness depends on it (spec §3.7).
"""

from typing import ClassVar, Final, Self, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Sequence
lazy from types import TracebackType

import grpc
lazy from pydantic import SecretStr

from ledger_fiscal_kit.ports import SignerPort, SignerUnavailableError
from ledger_kernel.errors import ErrorKind, InvalidValueError, LedgerError
from ledger_kernel.tenancy import Tenant
from ledger_platform.config.options import require_seconds
from ledger_platform.grpc.client import RemoteDefectError, RemoteError, call_stream_unary, call_unary
from ledger_platform.grpc.guard import MIN_TOKEN_LENGTH
from ledger_platform.grpc.metadata import TRACEPARENT_KEY, call_metadata
from ledger_platform.grpc.server import DEFAULT_MAX_MESSAGE_BYTES
from ledger_platform.runtime.calls import actor_of
from ledger_platform.runtime.facade import facade
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.settings import MAX_DEADLINE_SECONDS
from ledger_signer.api import CertificateNotFoundError, SignerApi
from ledger_signer.application.facade import port_error
from ledger_signer.domain.certificates import CertificateStatus
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
_COMPONENT: Final = "signer client"
_DEFECT_CODE: Final = LedgerError.code
"""What a chassis server answers a defect of its own with, beside ``INTERNAL``: ``internal``."""
_OPTIONS: Final = (
    ("grpc.max_send_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", DEFAULT_MAX_MESSAGE_BYTES),
)

type Metadata = tuple[tuple[str, str], ...]


class SignerTokenRefusedError(LedgerError):
    """The client's own token is shorter than the signer's guard takes: a defect of the deployment."""

    code: ClassVar[str] = "signer_token_refused"

    def __init__(self) -> None:
        super().__init__(f"the signer client's own token must be at least {MIN_TOKEN_LENGTH} characters")


def _common(error: RemoteError) -> LedgerError | None:
    """What ``error`` is for any call: a defect of the signer, or the signer unavailable; ``None``: neither.

    A transport's ``INTERNAL`` (no code of ours beside it) is the signer unavailable, as the platform has it.
    """
    if error.status is grpc.StatusCode.INTERNAL and error.remote_code == _DEFECT_CODE:
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


def _api_error(error: RemoteError) -> LedgerError | None:
    """The error any other call's ``error`` is (see the module); ``None``: ``error`` itself."""
    if error.kind is ErrorKind.NOT_FOUND and error.remote_code == CertificateNotFoundError.code:
        return CertificateNotFoundError()
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


@final
class SignerClient(SignerPort, SignerApi):
    """The signer, reached over gRPC with one caller's token (see the module)."""

    __slots__ = ("_address", "_channel", "_deadline", "_stub", "_token")

    def __init__(self, address: str, token: SecretStr, *, deadline: float) -> None:
        """A client of the signer at ``address`` (``host:port``), not yet open.

        ``SignerTokenRefusedError`` for a token shorter than ``MIN_TOKEN_LENGTH``; ``InvalidOptionError``
        for a deadline that is not a positive number of seconds up to ``MAX_DEADLINE_SECONDS``.
        """
        if len(token.get_secret_value()) < MIN_TOKEN_LENGTH:
            raise SignerTokenRefusedError
        require_seconds(_COMPONENT, "deadline", deadline, ceiling=MAX_DEADLINE_SECONDS)
        self._address = address
        self._token = token
        self._deadline = deadline
        self._channel: grpc.aio.Channel | None = None
        self._stub: SignerServiceAsyncStub | None = None

    @override
    def __repr__(self) -> str:
        return f"SignerClient(address={self._address!r}, deadline={self._deadline!r})"

    async def __aenter__(self) -> Self:
        """Open the channel; nothing is sent until the first call (grpc connects then)."""
        self._channel = grpc.aio.insecure_channel(self._address, options=_OPTIONS)
        self._stub = signer_pb2_grpc.SignerServiceStub(self._channel)
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the channel: calls in flight are cancelled, later ones answer the signer unavailable."""
        channel, self._channel, self._stub = self._channel, None, None
        if channel is not None:
            await channel.close()

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
        header: signer_pb2.ImportHeader,
        bundle: AsyncIterable[bytes] | None,
    ) -> ImportOutcome:
        stub = self._opened()

        async def requests() -> AsyncGenerator[signer_pb2.ImportCertificateRequest]:
            yield signer_pb2.ImportCertificateRequest(header=header)
            if bundle is None:
                return
            async for part in bundle:
                for start in range(0, len(part), CHUNK_BYTES):
                    yield signer_pb2.ImportCertificateRequest(chunk=part[start : start + CHUNK_BYTES])

        sent = call_stream_unary(
            stub.ImportCertificate,
            requests(),
            rpc=codec.IMPORT,
            deadline=self._deadline,
            metadata=self._metadata(call),
        )
        response = await _mapped(sent, _api_error)
        return codec.import_outcome(response)

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
        answer = await _mapped(sent, _api_error)
        return tuple(codec.certificate_view(certificate) for certificate in answer.certificates)

    @facade
    async def retire(
        self, call: Call | SystemCall, company: Tenant, certificate: CertificateId
    ) -> CertificateView:
        """``certificate`` of ``company`` retired for every mode, or as it is if it was already."""
        request = codec.retire_request(call, company, certificate)
        sent = self._unary(self._opened().RetireCertificate, request, call, codec.RETIRE)
        answer = await _mapped(sent, _api_error)
        return codec.certificate_view(answer.certificate)

    @facade
    async def import_local(
        self, call: Call | SystemCall, request: ImportRequest, password: Secret, bundle: AsyncIterable[bytes]
    ) -> ImportOutcome:
        """Install the certificate of the PKCS#12 ``bundle`` opened with ``password``."""
        return await self._import(call, codec.local_header(call, request, password), bundle)

    @facade
    async def import_remote(
        self, call: Call | SystemCall, request: ImportRequest, credential: Secret
    ) -> ImportOutcome:
        """Install the certificate an entity signs with for ``credential``."""
        return await self._import(call, codec.remote_header(call, request, credential), None)
