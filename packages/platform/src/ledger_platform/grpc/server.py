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
runs on the event loop. A synchronous method is left as it is: ``grpc.aio`` runs it on a thread, with
another kind of context, only so that code can migrate, and aborting from a synchronous generator after its
first item leaves the stream hanging until its deadline (grpc 1.84).
"""

import asyncio
import inspect
from contextlib import aclosing, asynccontextmanager
from time import perf_counter
from typing import ClassVar, Final, NamedTuple, TypeGuard, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from types import TracebackType

import grpc
import structlog
from prometheus_client import Histogram
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.config.options import InvalidOptionError, require_seconds
from ledger_platform.grpc.client import ERROR_CODE_KEY

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
_INTERNAL_CODE: Final = "internal"
_MAX_PORT: Final = 65535
_logger = structlog.stdlib.get_logger(__name__)


class GrpcBindError(LedgerError):
    """The server could not listen on its port: it is taken, or not ours to use. Nothing was started."""

    code: ClassVar[str] = "grpc_bind_failed"

    def __init__(self, port: int) -> None:
        super().__init__(f"the gRPC server could not bind port {port}")


type _Context = grpc.aio.ServicerContext[object, object]
"""The context ``grpc.aio`` hands a method. The guard only ends calls with it, whatever the messages are."""

type _CoroutineMethod[Input] = Callable[[Input, _Context], Awaitable[object]]
"""A method that answers with a coroutine, or one that streams by writing to its context.

``Input`` is a request, or for a method that streams its requests the iterator to read them from: an async
iterator at runtime, although ``types-grpcio`` types it as an ``Iterator``.
"""
type _GeneratorMethod[Input] = Callable[[Input, _Context], AsyncGenerator[object]]
"""A method that streams its responses by yielding them. ``Input`` is a request, or the requests to read."""


class _Answer(NamedTuple):
    """How a call that failed ends: the status, the details a client reads and the code beside them."""

    status: grpc.StatusCode
    details: str
    code: str


_DEFECT: Final = _Answer(_STATUS[ErrorKind.INTERNAL], "internal error", _INTERNAL_CODE)
"""What a client is told about a defect: no more than that there was one."""


def _report_defect(error: Exception, rpc: str) -> None:
    """Log where a defect happened and what it was, never the request: it may carry credentials."""
    code = error.code if isinstance(error, LedgerError) else _INTERNAL_CODE
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


def _ended_with(context: _Context, error: BaseException | None) -> grpc.StatusCode:
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

    def __init__(self, context: _Context, rpc: str, duration: Histogram) -> None:
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


def _is_coroutine_method[Input](
    behavior: Callable[[Input, grpc.ServicerContext], object],
) -> TypeGuard[_CoroutineMethod[Input]]:
    """Whether ``behavior`` is a coroutine function, as grpc decides it before it awaits one.

    ``types-grpcio`` types every behaviour as the synchronous kind (a plain return, the blocking context), so
    what grpc awaits with an asynchronous context has to be told apart here.
    """
    return inspect.iscoroutinefunction(behavior)


def _is_generator_method[Input](
    behavior: Callable[[Input, grpc.ServicerContext], object],
) -> TypeGuard[_GeneratorMethod[Input]]:
    """Whether ``behavior`` is an async generator function, as grpc decides it before it iterates one."""
    return inspect.isasyncgenfunction(behavior)


def _guard_coroutine[Input](
    behavior: _CoroutineMethod[Input], rpc: str, duration: Histogram
) -> _CoroutineMethod[Input]:
    async def guarded(request: Input, context: _Context) -> object:
        async with _Call(context, rpc, duration):
            return await behavior(request, context)

    return guarded


def _guard_generator[Input](
    behavior: _GeneratorMethod[Input], rpc: str, duration: Histogram
) -> _GeneratorMethod[Input]:
    async def guarded(request: Input, context: _Context) -> AsyncGenerator[object]:
        # When the call ends or fails, the method's generator is closed with this one, not left to the
        # garbage collector. A call abandoned by a client that left is finalized by asyncio.
        async with _Call(context, rpc, duration), aclosing(behavior(request, context)) as responses:
            async for response in responses:
                yield response

    return guarded


def _guard[Input](
    behavior: Callable[[Input, grpc.ServicerContext], object] | None, rpc: str, duration: Histogram
) -> _CoroutineMethod[Input] | _GeneratorMethod[Input] | None:
    """``behavior`` guarded; ``None`` when there is no behaviour or it is synchronous."""
    if behavior is None:
        return None
    if _is_generator_method(behavior):
        return _guard_generator(behavior, rpc, duration)
    if _is_coroutine_method(behavior):
        return _guard_coroutine(behavior, rpc, duration)
    return None


def _guarded[Request, Response](
    handler: grpc.RpcMethodHandler[Request, Response], rpc: str, duration: Histogram
) -> grpc.RpcMethodHandler[Request, Response]:
    """``handler`` with its behaviour guarded, or ``handler`` itself when it cannot be."""
    deserializer, serializer = handler.request_deserializer, handler.response_serializer
    if (guarded := _guard(handler.unary_unary, rpc, duration)) is not None:
        return grpc.unary_unary_rpc_method_handler(
            guarded, request_deserializer=deserializer, response_serializer=serializer
        )
    if (guarded := _guard(handler.unary_stream, rpc, duration)) is not None:
        return grpc.unary_stream_rpc_method_handler(
            guarded, request_deserializer=deserializer, response_serializer=serializer
        )
    if (guarded_reader := _guard(handler.stream_unary, rpc, duration)) is not None:
        return grpc.stream_unary_rpc_method_handler(
            guarded_reader, request_deserializer=deserializer, response_serializer=serializer
        )
    if (guarded_reader := _guard(handler.stream_stream, rpc, duration)) is not None:
        return grpc.stream_stream_rpc_method_handler(
            guarded_reader, request_deserializer=deserializer, response_serializer=serializer
        )
    return handler


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
        return _guarded(handler, handler_call_details.method, self._duration)


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


@asynccontextmanager
async def _serving(
    port: int, register: Callable[[grpc.aio.Server], None], registry: CollectorRegistry, grace: float
) -> AsyncGenerator[int]:
    server = grpc.aio.server(interceptors=(GuardInterceptor(registry),))
    register(server)
    bound = _bind(server, port)
    try:
        await server.start()
        yield bound
    finally:
        await server.stop(grace)


def serve_grpc(
    *,
    port: int,
    register: Callable[[grpc.aio.Server], None],
    registry: CollectorRegistry,
    grace: float = 10.0,
) -> AbstractAsyncContextManager[int]:
    """Serve the services ``register`` adds on ``port`` (0: any free port); yields the bound port.

    The calls of its asynchronous methods are timed into ``registry`` (see the module).

    The port is plaintext and open to whatever reaches it: until a service mesh adds mTLS (after go-live, spec
    §16.4), what protects a service is the network policy and the per-caller token and method allowlist its
    guard checks (ADR 0010), so a service that signs or holds secrets must not be served without both. On
    exit the server stops taking calls and gives those in flight ``grace`` seconds to finish before it cancels
    them.

    Raises ``InvalidOptionError``, at once, for a ``port`` outside 0..65535 or a ``grace`` that is not a
    positive, finite number of seconds; and ``GrpcBindError`` on entering when the port cannot be bound, with
    nothing left running.
    """
    _require_port(port)
    require_seconds(_COMPONENT, "grace", grace)
    return _serving(port, register, registry, grace)
