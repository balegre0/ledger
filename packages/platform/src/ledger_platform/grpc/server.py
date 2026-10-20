"""The gRPC server of a service: domain errors become gRPC statuses with a stable code.

A ``LedgerError`` a method raises ends the call with the status of its kind, its message as the details and
its ``code`` in the trailing metadata ``ledger-error-code``, as the HTTP application answers it with a status
and a problem document. A defect, an ``INTERNAL`` error of ours or any other exception, ends the call as
``INTERNAL`` with generic details and the code ``internal``: what went wrong goes to the log, never to the
client (grpc's own answer is ``UNKNOWN`` with the text of the exception).

Every call of an asynchronous method is timed into ``ledger_grpc_request_duration_seconds{rpc, code}``: the
method's full name, and the status the call ended with (``OK``, the status of an error, the one a method set
or aborted with, ``CANCELLED`` for a call the server's stop cut short). A method nobody registered is not
timed: its name is the client's choice, and each would be a series of its own.

All four kinds of method are guarded (unary or streaming request, unary or streaming response), when the
method is asynchronous: a coroutine function or an async generator function, the two shapes ``grpc.aio``
runs on the event loop. ``GuardInterceptor`` leaves a synchronous method as it is (``grpc.aio`` runs it on a
thread, with another kind of context, and aborting from a synchronous generator after its first item leaves
the stream hanging until its deadline, grpc 1.84); ``serve_grpc`` serves none, its caller guard
(``grpc.guard``) refuses them.

``serve_grpc`` places the caller guard inside ``GuardInterceptor``: a call its caller may not make is answered
and timed like any other failed call, before its method runs and before its request is read. A request is
read inside the guard (``_handlers``), so one that cannot be read is ``INVALID_ARGUMENT``
(``grpc_request_malformed``), answered and timed like any error, never grpc's ``UNKNOWN`` with the text.
"""

import asyncio
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import ClassVar, Final, NamedTuple, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from types import TracebackType

import grpc
import structlog
from opentelemetry import trace
from prometheus_client import Histogram
lazy from opentelemetry.trace import Tracer
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.config.options import InvalidOptionError, require_seconds
from ledger_platform.grpc._handlers import rewrapped
from ledger_platform.grpc.client import ERROR_CODE_KEY, INTERNAL_CODE
from ledger_platform.grpc.guard import CallerGuard
lazy from ledger_platform.grpc._handlers import Context, CoroutineMethod, GeneratorMethod
lazy from ledger_platform.grpc.guard import CallerTokens

_COMPONENT: Final = "gRPC"
"""What this module's option errors name (``InvalidOptionError.component``)."""

_STATUS: Final[Mapping[ErrorKind, grpc.StatusCode]] = {
    ErrorKind.INVALID: grpc.StatusCode.INVALID_ARGUMENT,
    ErrorKind.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    ErrorKind.CONFLICT: grpc.StatusCode.FAILED_PRECONDITION,
    ErrorKind.UNAUTHENTICATED: grpc.StatusCode.UNAUTHENTICATED,
    ErrorKind.FORBIDDEN: grpc.StatusCode.PERMISSION_DENIED,
    ErrorKind.UNAVAILABLE: grpc.StatusCode.UNAVAILABLE,
    ErrorKind.INTERNAL: grpc.StatusCode.INTERNAL,
}
_MAX_PORT: Final = 65535
DEFAULT_MAX_MESSAGE_BYTES: Final = 5 * 1024 * 1024
"""The largest message a server takes or sends by default: a certificate's chunk, a signed document."""
MAX_MESSAGE_BYTES: Final = 64 * 1024 * 1024
"""The largest limit a server may be given."""
DEFAULT_MAX_CONCURRENT_RPCS: Final = 64
"""The most calls a server takes at once by default: past it a call ends ``RESOURCE_EXHAUSTED`` at once."""
MAX_CONCURRENT_RPCS: Final = 10_000
"""The largest number of calls at once a server may be given."""
_logger = structlog.stdlib.get_logger(__name__)


class GrpcBindError(LedgerError):
    """The server could not listen on its port: it is taken, or not ours to use. Nothing was started."""

    code: ClassVar[str] = "grpc_bind_failed"

    def __init__(self, port: int) -> None:
        super().__init__(f"the gRPC server could not bind port {port}")


class _Answer(NamedTuple):
    """How a call that failed ends: the status, the details a client reads and the code beside them."""

    status: grpc.StatusCode
    details: str
    code: str


_DEFECT: Final = _Answer(_STATUS[ErrorKind.INTERNAL], "internal error", INTERNAL_CODE)
"""What a client is told about a defect: no more than that there was one."""


def _report_defect(error: Exception, rpc: str) -> None:
    """Log where a defect happened and what it was, never the request: it may carry credentials."""
    code = error.code if isinstance(error, LedgerError) else INTERNAL_CODE
    _logger.error("grpc.unhandled_error", rpc=rpc, code=code, exc_info=error)


def _answer(error: BaseException | None, rpc: str) -> _Answer | None:
    """How a call that ended with ``error`` is answered; ``None`` when it is not ours to answer.

    A ``LedgerError`` that is not ``INTERNAL`` says what it is. Any other error is a defect of ours: it is
    logged and the client is told no more than that there was one. What is not ours to answer passes: a
    cancellation (a client that left, a server that stops) is no ``Exception``, and grpc's own errors
    (``grpc.aio.BaseError``: an ``abort`` the method made itself, a client that is gone, a server that is
    stopping) are grpc's to deal with.
    """
    if not isinstance(error, Exception) or isinstance(error, grpc.aio.BaseError):
        return None
    if isinstance(error, LedgerError) and error.kind is not ErrorKind.INTERNAL:
        return _Answer(_STATUS[error.kind], str(error), error.code)
    _report_defect(error, rpc)
    return _DEFECT


def _set(code: object) -> grpc.StatusCode | None:
    """The status a method set or aborted with, if it did.

    ``context.code()`` is ``None`` until a method sets one, whatever ``types-grpcio`` says.
    """
    return code if isinstance(code, grpc.StatusCode) else None


def _ended_with(context: Context, error: BaseException | None) -> grpc.StatusCode:
    """The status of a call that was not ours to answer."""
    if (chosen := _set(context.code())) is not None:
        return chosen
    if error is None:
        return grpc.StatusCode.OK
    return grpc.StatusCode.CANCELLED if isinstance(error, asyncio.CancelledError) else grpc.StatusCode.UNKNOWN


@final
class _Call:
    """A call of an asynchronous method, made to ``rpc`` (``/package.Service/Method``).

    Its errors are answered with a status (see ``_answer``), and its duration is measured under the status it
    ended with.
    """

    __slots__ = ("_context", "_duration", "_rpc", "_started")

    def __init__(self, context: Context, rpc: str, duration: Histogram) -> None:
        self._context = context
        self._rpc = rpc
        self._duration = duration
        self._started = perf_counter()

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        answer = _answer(exc, self._rpc)
        status = _ended_with(self._context, exc) if answer is None else answer.status
        self._duration.labels(self._rpc, status.name).observe(perf_counter() - self._started)
        if answer is not None:
            await self._context.abort(
                answer.status, answer.details, trailing_metadata=((ERROR_CODE_KEY, answer.code),)
            )


@final
class _Guarded:
    """Wraps the asynchronous methods of a call to ``rpc`` in a ``_Call``; leaves a synchronous one alone.

    The request is read inside the ``_Call``: one that cannot be read is answered and timed like any error.
    """

    __slots__ = ("_duration", "_rpc")

    def __init__(self, rpc: str, duration: Histogram) -> None:
        self._rpc = rpc
        self._duration = duration

    def coroutine[Raw, Input](
        self, behavior: CoroutineMethod[Input], parse: Callable[[Raw], Input]
    ) -> CoroutineMethod[Raw]:
        rpc, duration = self._rpc, self._duration

        async def guarded(request: Raw, context: Context) -> object:
            async with _Call(context, rpc, duration):
                return await behavior(parse(request), context)

        return guarded

    def generator[Raw, Input](
        self, behavior: GeneratorMethod[Input], parse: Callable[[Raw], Input]
    ) -> GeneratorMethod[Raw]:
        rpc, duration = self._rpc, self._duration

        async def guarded(request: Raw, context: Context) -> AsyncGenerator[object]:
            # When the call ends or fails, the method's generator is closed with this one, not left to the
            # garbage collector. A call abandoned by a client that left is finalized by asyncio.
            async with (
                _Call(context, rpc, duration),
                aclosing(behavior(parse(request), context)) as responses,
            ):
                async for response in responses:
                    yield response

        return guarded

    def synchronous(self) -> CoroutineMethod[object] | None:
        return None


@final
class GuardInterceptor(grpc.aio.ServerInterceptor):
    """Guards every asynchronous method, of any of the four kinds: answers its errors and times its calls.

    A ``LedgerError`` becomes the status of its kind and its code in ``ledger-error-code``; a defect becomes
    ``INTERNAL``, ``internal`` and nothing more (see the module). A method that aborts the call itself keeps
    the status it chose, and grpc's own errors are left to grpc. Every call is timed into
    ``ledger_grpc_request_duration_seconds{rpc, code}``, registered in ``registry``.
    """

    __slots__ = ("_duration",)

    def __init__(self, registry: CollectorRegistry) -> None:
        self._duration = Histogram(
            "ledger_grpc_request_duration_seconds",
            "Time to answer a gRPC call, by method and status code.",
            ["rpc", "code"],
            registry=registry,
        )

    @override
    async def intercept_service[Request, Response](
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler[Request, Response] | None]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Request, Response] | None:
        handler = await continuation(handler_call_details)
        if handler is None:
            return None  # no such method: grpc answers UNIMPLEMENTED
        return rewrapped(handler, _Guarded(handler_call_details.method, self._duration))


def _require_port(port: int) -> None:
    if not 0 <= port <= _MAX_PORT:
        raise InvalidOptionError(
            component=_COMPONENT, option="port", requirement=f"between 0 and {_MAX_PORT}", value=port
        )


def _bind(server: grpc.aio.Server, port: int) -> int:
    """The port ``server`` listens on, on every interface. grpc raises when it cannot: ``GrpcBindError``."""
    try:
        return server.add_insecure_port(f"[::]:{port}")
    except RuntimeError as error:
        raise GrpcBindError(port) from error


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ServerOptions:
    """How a server runs, besides what it serves and who may call it. Checked when made.

    Raises ``InvalidOptionError`` for a ``grace`` that is not a positive, finite number of seconds, a
    ``max_message_bytes`` outside 1..``MAX_MESSAGE_BYTES``, or a ``max_concurrent_rpcs`` outside
    1..``MAX_CONCURRENT_RPCS``.
    """

    grace: float = 10.0
    """Seconds the calls in flight have to finish when the server stops, before they are cancelled."""
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    """The largest message the server takes or sends: a larger one ends its call ``RESOURCE_EXHAUSTED``."""
    max_concurrent_rpcs: int = DEFAULT_MAX_CONCURRENT_RPCS
    """The most calls the server runs at once: one more ends ``RESOURCE_EXHAUSTED`` before it is admitted,
    so a flood of calls cannot hold the process's memory and threads."""
    tracer: Tracer | None = None
    """What traces each call (the app's, ``Telemetry.tracer``); the process-wide provider's when ``None``."""

    def __post_init__(self) -> None:
        require_seconds(_COMPONENT, "grace", self.grace)
        if not 0 < self.max_message_bytes <= MAX_MESSAGE_BYTES:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="max_message_bytes",
                requirement=f"1 to {MAX_MESSAGE_BYTES} bytes",
                value=self.max_message_bytes,
            )
        if not 0 < self.max_concurrent_rpcs <= MAX_CONCURRENT_RPCS:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="max_concurrent_rpcs",
                requirement=f"1 to {MAX_CONCURRENT_RPCS} calls",
                value=self.max_concurrent_rpcs,
            )


DEFAULT_SERVER_OPTIONS: Final = ServerOptions()


@asynccontextmanager
async def _serving(
    port: int,
    register: Callable[[grpc.aio.Server], None],
    interceptors: tuple[grpc.aio.ServerInterceptor, ...],
    options: ServerOptions,
) -> AsyncGenerator[int]:
    server = grpc.aio.server(
        interceptors=interceptors,
        maximum_concurrent_rpcs=options.max_concurrent_rpcs,
        options=(
            ("grpc.max_receive_message_length", options.max_message_bytes),
            ("grpc.max_send_message_length", options.max_message_bytes),
        ),
    )
    register(server)
    bound = _bind(server, port)
    try:
        await server.start()
        yield bound
    finally:
        await server.stop(options.grace)


def serve_grpc(
    *,
    port: int,
    register: Callable[[grpc.aio.Server], None],
    registry: CollectorRegistry,
    callers: CallerTokens,
    options: ServerOptions = DEFAULT_SERVER_OPTIONS,
) -> AbstractAsyncContextManager[int]:
    """Serve the services ``register`` adds on ``port`` (0: any free port), guarded; yields the bound port.

    Every call of a registered method is admitted by ``callers`` before the method runs (``grpc.guard``: its
    caller's token and allowlist), traced as a server span that continues the caller's trace, and timed into
    ``registry`` (see the module). There is no way to serve without a guard.

    The port is plaintext and open to whatever reaches it: until a service mesh adds mTLS (after go-live, spec
    §16.4), what protects a service is the network policy and the per-caller token and method allowlist its
    guard checks (ADR 0010). On exit the server stops taking calls and gives those in flight
    ``options.grace`` seconds to finish before it cancels them.

    Raises ``InvalidOptionError``, at once, for a ``port`` outside 0..65535; and ``GrpcBindError`` on entering
    when the port cannot be bound, with nothing left running.
    """
    _require_port(port)
    tracer = trace.get_tracer(__name__) if options.tracer is None else options.tracer
    interceptors = (GuardInterceptor(registry), CallerGuard(callers, tracer))
    return _serving(port, register, interceptors, options)
