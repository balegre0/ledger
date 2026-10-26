"""``ledger.signer.v1.SignerService`` over the use cases, served behind the platform's guard (spec §3.7).

Each method reads its request with the codec (a field that does not read is ``grpc_request_malformed``: our
callers' defect, said without the value, as is a call id the request and the call's metadata do not both
name), acts for the call's actor and call id (``acting_as``: the audit names who asked, as the api or the
worker forwarded it) and answers with the codec; a signature names its
caller too (``current_caller``, as the guard admitted it). The use cases' errors cross as they are: the
platform answers each with the status of its kind and its code (``grpc.server``), a defect (custody damaged
or tampered with, a KMS that refuses) as ``INTERNAL`` saying no more.

An import streams: its header first (anything else first, or a header again, is
``signer_import_header_first``), then, for a bundle, its chunks, kept in memory up to ``MAX_BUNDLE_BYTES``
(past it ``signer_import_too_large``, nothing more read); a remote import is its header alone (a chunk after
it is ``signer_import_chunks_unexpected``); a stream that has not ended within ``import_seconds`` (held open,
or trickling empty chunks) is ``signer_import_too_slow``. The stream's refusals are the codec's. Nothing is
admitted, sealed or kept until the stream has ended well. The password and the credential go from the
header to custody as ``Secret`` values, never into a log, a span or an answer.

``CALLER_METHODS`` is the allowlists (plan Decision 12): the api imports, describes and retires; the worker
and the receiver sign and describe. ``SignerServer`` is the resource the signer's app opens: the service on
its port (0: any free one, ``port`` says which), traced by the app's tracer, and its readiness while it
serves. No gRPC health service is registered: the signer's probes are HTTP, and the guard would refuse an
unauthenticated health call anyway.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import ClassVar, Final, Self, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
lazy from types import TracebackType

import grpc
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_platform.data.audit import acting_as
from ledger_platform.grpc import MalformedRequestError, current_caller, serve_grpc
from ledger_platform.grpc.metadata import call_id_of
from ledger_signer.adapters.grpc import codec
from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.codec import (
    ImportChunksUnexpectedError,
    ImportHeaderFirstError,
    ImportTooSlowError,
)
from ledger_signer.application.importing import MAX_BUNDLE_BYTES, ImportTooLargeError
from ledger_signer.application.ports import Secret
lazy from ledger_platform.data.audit import Actor
lazy from ledger_platform.grpc import CallerTokens
lazy from ledger_platform.grpc.server import ServerOptions
lazy from ledger_signer.adapters.settings import Caller
lazy from ledger_signer.application.certificates import Describing, Retiring
lazy from ledger_signer.application.importing import Importing
lazy from ledger_signer.application.ports import ImportOutcome
lazy from ledger_signer.application.signing import Signing

type Context[Request, Response] = grpc.aio.ServicerContext[Request, Response]

_SIGNING: Final = frozenset({codec.SIGN_XML, codec.SIGN_SEED, codec.DESCRIBE})
CALLER_METHODS: Final[Mapping[Caller, frozenset[str]]] = {
    "api": frozenset({codec.IMPORT, codec.DESCRIBE, codec.RETIRE, codec.SIGN_XML}),
    "worker": _SIGNING,
    "receiver": _SIGNING,
}
"""What each caller may call, by full name (Decision 12). The api signs documents too, never a seed: the
synchronous numbering-and-signing path of ``POST /v1/invoices`` runs in it (spec §8.5, amended 2026-10-26
by v2-M3b Task 26, which found the api refused there)."""
_HEADER: Final = "header"
_CHUNK: Final = "chunk"


class SignerNotServingError(LedgerError):
    """The signer's gRPC service asked for while it does not serve (not yet started, or stopped)."""

    code: ClassVar[str] = "signer_not_serving"

    def __init__(self) -> None:
        super().__init__("the signer's gRPC service is not serving")


def _actor[Request, Response](call: signer_pb2.CallContext, context: Context[Request, Response]) -> Actor:
    """Who acts in the call: the actor ``call`` names, in the call id the call's metadata names too.

    A call id the metadata does not repeat is malformed: the logs (the guard's, by the metadata) and the
    audit (by the request) would name two calls.
    """
    actor = _read(partial(codec.call_actor, call))
    if call_id_of(tuple(context.invocation_metadata() or ())) != actor.call_id:
        raise MalformedRequestError
    return actor


def _read[T](read: Callable[[], T]) -> T:
    """What ``read`` reads of a request; a field it refuses makes the request malformed (said without it)."""
    try:
        return read()
    except InvalidValueError as error:
        raise MalformedRequestError from error


async def _header(requests: AsyncIterator[signer_pb2.ImportCertificateRequest]) -> signer_pb2.ImportHeader:
    """The import's header: its first message."""
    first = await anext(requests, None)
    if first is None or first.WhichOneof("part") != _HEADER:
        raise ImportHeaderFirstError
    return first.header


async def _bundle(requests: AsyncIterator[signer_pb2.ImportCertificateRequest]) -> bytes:
    """The bundle the chunks after the header carry, refused as soon as it passes ``MAX_BUNDLE_BYTES``."""
    bundle = bytearray()
    async for request in requests:
        if request.WhichOneof("part") != _CHUNK:
            raise ImportHeaderFirstError
        if len(bundle) + len(request.chunk) > MAX_BUNDLE_BYTES:
            raise ImportTooLargeError
        bundle += request.chunk
    return bytes(bundle)


async def _nothing_after(requests: AsyncIterator[signer_pb2.ImportCertificateRequest]) -> None:
    """A remote import's stream ends with its header."""
    if await anext(requests, None) is not None:
        raise ImportChunksUnexpectedError


@final
class SignerServicer(signer_pb2_grpc.SignerServiceServicer):
    """The signer's service over its use cases (see the module)."""

    __slots__ = ("_describing", "_import_seconds", "_importing", "_retiring", "_signing")

    def __init__(
        self,
        *,
        importing: Importing,
        describing: Describing,
        retiring: Retiring,
        signing: Signing,
        import_seconds: float,
    ) -> None:
        self._importing = importing
        self._describing = describing
        self._retiring = retiring
        self._signing = signing
        self._import_seconds = import_seconds

    @asynccontextmanager
    async def _reading_in_time(self) -> AsyncGenerator[None]:
        """An import's stream read within ``import_seconds``, or ``ImportTooSlowError``."""
        try:
            async with asyncio.timeout(self._import_seconds):
                yield
        except TimeoutError:
            raise ImportTooSlowError from None

    @override
    async def ImportCertificate(
        self,
        request_iterator: AsyncIterator[signer_pb2.ImportCertificateRequest],
        context: Context[signer_pb2.ImportCertificateRequest, signer_pb2.ImportCertificateResponse],
    ) -> signer_pb2.ImportCertificateResponse:
        async with self._reading_in_time():
            header = await _header(request_iterator)
            actor = _actor(header.call, context)
            request = _read(partial(codec.import_request, header))
            bundle: bytes | None
            match header.WhichOneof("source"):
                case "local":
                    bundle = await _bundle(request_iterator)
                case "remote":
                    await _nothing_after(request_iterator)
                    bundle = None
                case _:
                    raise MalformedRequestError
        outcome: ImportOutcome
        with acting_as(actor):
            if bundle is None:
                outcome = await self._importing.remote(request, Secret(header.remote.credential))
            else:
                outcome = await self._importing.local(request, bundle, Secret(header.local.password))
        return codec.import_response(outcome)

    @override
    async def DescribeCertificates(
        self,
        request: signer_pb2.DescribeCertificatesRequest,
        context: Context[signer_pb2.DescribeCertificatesRequest, signer_pb2.DescribeCertificatesResponse],
    ) -> signer_pb2.DescribeCertificatesResponse:
        actor = _actor(request.call, context)
        tenant = _read(partial(codec.described, request))
        with acting_as(actor):
            views = await self._describing.certificates(tenant, include_retired=request.include_retired)
        return signer_pb2.DescribeCertificatesResponse(
            certificates=[codec.certificate_wire(view) for view in views]
        )

    @override
    async def RetireCertificate(
        self,
        request: signer_pb2.RetireCertificateRequest,
        context: Context[signer_pb2.RetireCertificateRequest, signer_pb2.RetireCertificateResponse],
    ) -> signer_pb2.RetireCertificateResponse:
        actor = _actor(request.call, context)
        company, certificate = _read(partial(codec.retirement, request))
        with acting_as(actor):
            view = await self._retiring.retire(company, certificate)
        return signer_pb2.RetireCertificateResponse(certificate=codec.certificate_wire(view))

    @override
    async def SignXml(
        self,
        request: signer_pb2.SignXmlRequest,
        context: Context[signer_pb2.SignXmlRequest, signer_pb2.SignXmlResponse],
    ) -> signer_pb2.SignXmlResponse:
        actor = _actor(request.call, context)
        document = _read(partial(codec.xml_to_sign, request))
        with acting_as(actor):
            signed = await self._signing.sign_xml(current_caller(), document)
        return signer_pb2.SignXmlResponse(
            signed_document=signed.document, certificate_id=str(signed.certificate)
        )

    @override
    async def SignSeed(
        self,
        request: signer_pb2.SignSeedRequest,
        context: Context[signer_pb2.SignSeedRequest, signer_pb2.SignSeedResponse],
    ) -> signer_pb2.SignSeedResponse:
        actor = _actor(request.call, context)
        seed = _read(partial(codec.seed_to_sign, request))
        with acting_as(actor):
            signed = await self._signing.sign_seed(current_caller(), seed)
        return signer_pb2.SignSeedResponse(
            signed_seed=signed.document, certificate_id=str(signed.certificate)
        )


@final
class SignerServer:
    """The signer's gRPC service as a resource: served on enter, stopped on exit (see the module)."""

    __slots__ = ("_callers", "_options", "_port", "_registry", "_servicer", "_stack", "_wanted")

    def __init__(
        self,
        servicer: SignerServicer,
        *,
        port: int,
        registry: CollectorRegistry,
        callers: CallerTokens,
        options: ServerOptions,
    ) -> None:
        self._servicer = servicer
        self._wanted = port
        self._registry = registry
        self._callers = callers
        self._options = options
        self._stack: AsyncExitStack | None = None
        self._port: int | None = None

    @override
    def __repr__(self) -> str:
        return f"SignerServer(port={self._wanted!r}, callers={self._callers!r})"

    def _register(self, server: grpc.aio.Server) -> None:
        signer_pb2_grpc.add_SignerServiceServicer_to_server(self._servicer, server)

    async def __aenter__(self) -> Self:
        """Serve; ``GrpcBindError`` when the port cannot be bound (nothing is left running)."""
        stack = AsyncExitStack()
        serving = serve_grpc(
            port=self._wanted,
            register=self._register,
            registry=self._registry,
            callers=self._callers,
            options=self._options,
        )
        self._port = await stack.enter_async_context(serving)
        self._stack = stack
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Stop: the calls in flight get the options' grace, then are cancelled."""
        stack, self._stack, self._port = self._stack, None, None
        if stack is not None:
            await stack.__aexit__(kind, error, traceback)

    @property
    def port(self) -> int:
        """The port it serves on (``SignerNotServingError`` while it does not)."""
        if self._port is None:
            raise SignerNotServingError
        return self._port

    async def ready(self) -> None:
        """Answer while it serves (the signer's ``grpc`` readiness check), raise otherwise."""
        _ = self.port
