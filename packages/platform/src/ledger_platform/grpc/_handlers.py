"""Method handlers rebuilt around their behaviour, whichever of the four kinds of method they serve.

``grpc.aio`` runs a method on the event loop when it is a coroutine function or an async generator function,
and on a thread otherwise. An interceptor that wraps methods (the server's guard, the caller guard) says what
becomes of each shape through a ``MethodWrapper``; ``rewrapped`` rebuilds the handler around the result.
"""

import inspect
from abc import ABCMeta, abstractmethod
from typing import Protocol, TypeGuard
lazy from collections.abc import AsyncGenerator, Awaitable, Callable

import grpc

type Context = grpc.aio.ServicerContext[object, object]
"""The context ``grpc.aio`` hands a method. A wrapper only ends calls with it, whatever the messages are."""

type CoroutineMethod[Input] = Callable[[Input, Context], Awaitable[object]]
"""A method that answers with a coroutine, or one that streams by writing to its context.

``Input`` is a request, or for a method that streams its requests the iterator to read them from: an async
iterator at runtime, although ``types-grpcio`` types it as an ``Iterator``.
"""
type GeneratorMethod[Input] = Callable[[Input, Context], AsyncGenerator[object]]
"""A method that streams its responses by yielding them. ``Input`` is a request, or the requests to read."""
type SynchronousMethod[Input] = Callable[[Input, grpc.ServicerContext], object]
"""A method ``grpc.aio`` runs on a thread, with the blocking kind of context."""


class MethodWrapper(Protocol, metaclass=ABCMeta):
    """What becomes of a method of each shape: a wrapped one, or ``None`` to leave the handler as it is.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    def coroutine[Input](self, behavior: CoroutineMethod[Input]) -> CoroutineMethod[Input]:
        """``behavior``, a coroutine function, wrapped."""

    @abstractmethod
    def generator[Input](self, behavior: GeneratorMethod[Input]) -> GeneratorMethod[Input]:
        """``behavior``, an async generator function, wrapped."""

    @abstractmethod
    def synchronous[Input](self, behavior: SynchronousMethod[Input]) -> CoroutineMethod[Input] | None:
        """What serves in place of ``behavior``, a synchronous method; ``None`` leaves it as it is."""


def _is_coroutine_method[Input](behavior: SynchronousMethod[Input]) -> TypeGuard[CoroutineMethod[Input]]:
    """Whether ``behavior`` is a coroutine function, as grpc decides it before it awaits one.

    ``types-grpcio`` types every behaviour as the synchronous kind (a plain return, the blocking context), so
    what grpc awaits with an asynchronous context has to be told apart here.
    """
    return inspect.iscoroutinefunction(behavior)


def _is_generator_method[Input](behavior: SynchronousMethod[Input]) -> TypeGuard[GeneratorMethod[Input]]:
    """Whether ``behavior`` is an async generator function, as grpc decides it before it iterates one."""
    return inspect.isasyncgenfunction(behavior)


def _wrap[Input](
    behavior: SynchronousMethod[Input] | None, wrapper: MethodWrapper
) -> CoroutineMethod[Input] | GeneratorMethod[Input] | None:
    """``behavior`` wrapped; ``None`` when there is no behaviour or the wrapper leaves it."""
    if behavior is None:
        return None
    if _is_generator_method(behavior):
        return wrapper.generator(behavior)
    if _is_coroutine_method(behavior):
        return wrapper.coroutine(behavior)
    return wrapper.synchronous(behavior)


def rewrapped[Request, Response](
    handler: grpc.RpcMethodHandler[Request, Response], wrapper: MethodWrapper
) -> grpc.RpcMethodHandler[Request, Response]:
    """``handler`` with its behaviour wrapped, or ``handler`` itself when the wrapper leaves it."""
    deserializer, serializer = handler.request_deserializer, handler.response_serializer
    if (wrapped := _wrap(handler.unary_unary, wrapper)) is not None:
        return grpc.unary_unary_rpc_method_handler(
            wrapped, request_deserializer=deserializer, response_serializer=serializer
        )
    if (wrapped := _wrap(handler.unary_stream, wrapper)) is not None:
        return grpc.unary_stream_rpc_method_handler(
            wrapped, request_deserializer=deserializer, response_serializer=serializer
        )
    if (wrapped_reader := _wrap(handler.stream_unary, wrapper)) is not None:
        return grpc.stream_unary_rpc_method_handler(
            wrapped_reader, request_deserializer=deserializer, response_serializer=serializer
        )
    if (wrapped_reader := _wrap(handler.stream_stream, wrapper)) is not None:
        return grpc.stream_stream_rpc_method_handler(
            wrapped_reader, request_deserializer=deserializer, response_serializer=serializer
        )
    return handler
