"""Method handlers rebuilt around their behaviour, whichever of the four kinds of method they serve.

``grpc.aio`` runs a method on the event loop when it is a coroutine function or an async generator function,
and on a thread otherwise. An interceptor that wraps methods (the server's guard, the caller guard) says what
becomes of each shape through a ``MethodWrapper``; ``rewrapped`` rebuilds the handler around the result.

A rebuilt handler has no request deserializer: grpc hands the wrapper the request as it came (bytes, or an
iterator of them), and the wrapper reads it with the ``parse`` it is given, when it chooses to. So nothing of
a request is parsed before the caller guard admits the call, and a request that cannot be read is
``MalformedRequestError`` (``INVALID_ARGUMENT``, ``grpc_request_malformed``) raised inside the wrapper, never
grpc's own ``UNKNOWN`` with the deserializer's exception, which can quote the request.
"""

import inspect
from abc import ABCMeta, abstractmethod
from typing import ClassVar, Protocol, TypeGuard
lazy from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator

import grpc

from ledger_kernel.errors import InvalidInputError

type Context = grpc.aio.ServicerContext[object, object]
"""The context ``grpc.aio`` hands a method. A wrapper only ends calls with it, whatever the messages are."""

type CoroutineMethod[Input] = Callable[[Input, Context], Awaitable[object]]
"""A method that answers with a coroutine, or one that streams by writing to its context.

``Input`` is a request, or for a method that streams its requests the iterator to read them from.
"""
type GeneratorMethod[Input] = Callable[[Input, Context], AsyncGenerator[object]]
"""A method that streams its responses by yielding them. ``Input`` is a request, or the requests to read."""
type SynchronousMethod[Input] = Callable[[Input, grpc.ServicerContext], object]
"""A method as ``types-grpcio`` types every one: the kind ``grpc.aio`` runs on a thread."""


class MalformedRequestError(InvalidInputError):
    """A request its method's deserializer could not read. The message says no more: a reason may quote it."""

    code: ClassVar[str] = "grpc_request_malformed"

    def __init__(self) -> None:
        super().__init__("the request could not be read")


class MethodWrapper(Protocol, metaclass=ABCMeta):
    """What becomes of a method of each shape: a wrapped one, or ``None`` to leave the handler as it is.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach. The
    metaclass is what lets them be (basedpyright refuses an ``abstractmethod`` of a class that is not
    abstract), as in ``auth.access.PermissionSource``.
    """

    @abstractmethod
    def coroutine[Raw, Input](
        self, behavior: CoroutineMethod[Input], parse: Callable[[Raw], Input]
    ) -> CoroutineMethod[Raw]:
        """``behavior``, a coroutine function, wrapped; what it is given read by ``parse`` first."""

    @abstractmethod
    def generator[Raw, Input](
        self, behavior: GeneratorMethod[Input], parse: Callable[[Raw], Input]
    ) -> GeneratorMethod[Raw]:
        """``behavior``, an async generator function, wrapped; what it is given read by ``parse`` first."""

    @abstractmethod
    def synchronous(self) -> CoroutineMethod[object] | None:
        """What serves in place of a synchronous method (never calling it); ``None`` leaves it as it is."""


def _is_coroutine_method[Input](behavior: SynchronousMethod[Input]) -> TypeGuard[CoroutineMethod[Input]]:
    """Whether ``behavior`` is a coroutine function, as grpc decides it before it awaits one.

    ``types-grpcio`` types every behaviour as the synchronous kind (a plain return, the blocking context), so
    what grpc awaits with an asynchronous context has to be told apart here.
    """
    return inspect.iscoroutinefunction(behavior)


def _is_generator_method[Input](behavior: SynchronousMethod[Input]) -> TypeGuard[GeneratorMethod[Input]]:
    """Whether ``behavior`` is an async generator function, as grpc decides it before it iterates one."""
    return inspect.isasyncgenfunction(behavior)


def _is_coroutine_reader[Request](
    behavior: SynchronousMethod[Iterator[Request]],
) -> TypeGuard[CoroutineMethod[AsyncIterator[Request]]]:
    """Whether ``behavior``, a method that streams its requests, is a coroutine function.

    ``grpc.aio`` then hands it an async iterator of them, although ``types-grpcio`` types it as an
    ``Iterator``.
    """
    return inspect.iscoroutinefunction(behavior)


def _is_generator_reader[Request](
    behavior: SynchronousMethod[Iterator[Request]],
) -> TypeGuard[GeneratorMethod[AsyncIterator[Request]]]:
    """Whether ``behavior``, a method that streams its requests, is an async generator function."""
    return inspect.isasyncgenfunction(behavior)


def _wrap_one[Raw, Request](
    behavior: SynchronousMethod[Request] | None, parse: Callable[[Raw], Request], wrapper: MethodWrapper
) -> CoroutineMethod[Raw] | GeneratorMethod[Raw] | None:
    """A method that takes one request, wrapped; ``None`` when there is none or the wrapper leaves it."""
    if behavior is None:
        return None
    if _is_generator_method(behavior):
        return wrapper.generator(behavior, parse)
    if _is_coroutine_method(behavior):
        return wrapper.coroutine(behavior, parse)
    return wrapper.synchronous()


def _wrap_many[Raw, Request](
    behavior: SynchronousMethod[Iterator[Request]] | None,
    parse: Callable[[AsyncIterator[Raw]], AsyncIterator[Request]],
    wrapper: MethodWrapper,
) -> CoroutineMethod[AsyncIterator[Raw]] | GeneratorMethod[AsyncIterator[Raw]] | None:
    """A method that streams its requests, wrapped; ``None`` when there is none or the wrapper leaves it."""
    if behavior is None:
        return None
    if _is_generator_reader(behavior):
        return wrapper.generator(behavior, parse)
    if _is_coroutine_reader(behavior):
        return wrapper.coroutine(behavior, parse)
    return wrapper.synchronous()


def _same[Value](value: Value) -> Value:
    """What a handler without a deserializer is handed is what its method takes."""
    return value


def _reader[Request](deserialize: Callable[[bytes], Request]) -> Callable[[bytes], Request]:
    """Reads one request with ``deserialize``; ``MalformedRequestError`` when it cannot."""

    def read(raw: bytes) -> Request:
        try:
            return deserialize(raw)
        except Exception as error:
            raise MalformedRequestError from error

    return read


def _stream_reader[Request](
    deserialize: Callable[[bytes], Request],
) -> Callable[[AsyncIterator[bytes]], AsyncIterator[Request]]:
    """Reads each request of a stream with ``deserialize`` as the method takes it."""
    read = _reader(deserialize)

    async def read_each(raw: AsyncIterator[bytes]) -> AsyncGenerator[Request]:
        async for message in raw:
            yield read(message)

    return read_each


def _rebuilt[Raw, Request, Response](
    handler: grpc.RpcMethodHandler[Request, Response],
    wrapper: MethodWrapper,
    one: Callable[[Raw], Request],
    many: Callable[[AsyncIterator[Raw]], AsyncIterator[Request]],
) -> grpc.RpcMethodHandler[Request, Response]:
    serializer = handler.response_serializer
    if (wrapped := _wrap_one(handler.unary_unary, one, wrapper)) is not None:
        return grpc.unary_unary_rpc_method_handler(wrapped, response_serializer=serializer)
    if (wrapped := _wrap_one(handler.unary_stream, one, wrapper)) is not None:
        return grpc.unary_stream_rpc_method_handler(wrapped, response_serializer=serializer)
    if (wrapped_reader := _wrap_many(handler.stream_unary, many, wrapper)) is not None:
        return grpc.stream_unary_rpc_method_handler(wrapped_reader, response_serializer=serializer)
    if (wrapped_reader := _wrap_many(handler.stream_stream, many, wrapper)) is not None:
        return grpc.stream_stream_rpc_method_handler(wrapped_reader, response_serializer=serializer)
    return handler


def rewrapped[Request, Response](
    handler: grpc.RpcMethodHandler[Request, Response], wrapper: MethodWrapper
) -> grpc.RpcMethodHandler[Request, Response]:
    """``handler`` with its behaviour wrapped and its requests read inside it (see the module).

    ``handler`` itself when the wrapper leaves it (a synchronous method): grpc then reads its requests.
    """
    deserialize = handler.request_deserializer
    if deserialize is None:
        return _rebuilt(handler, wrapper, _same, _same)
    return _rebuilt(handler, wrapper, _reader(deserialize), _stream_reader(deserialize))
