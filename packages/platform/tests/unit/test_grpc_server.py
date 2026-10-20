import asyncio
import contextlib
from dataclasses import dataclass
from math import inf, isfinite, nan
from typing import ClassVar, Final
lazy from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence

import grpc
import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from prometheus_client import CollectorRegistry
from pydantic import SecretStr
from structlog.testing import capture_logs

from ledger_kernel.errors import (
    ConflictError,
    ErrorKind,
    ForbiddenError,
    InvalidInputError,
    LedgerError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
)
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.grpc.client import ERROR_CODE_KEY
from ledger_platform.grpc.guard import CallerPolicy, CallerTokens
from ledger_platform.grpc.metadata import call_metadata
from ledger_platform.grpc.server import (
    DEFAULT_MAX_CONCURRENT_RPCS,
    MAX_CONCURRENT_RPCS,
    MAX_MESSAGE_BYTES,
    GrpcBindError,
    GuardInterceptor,
    ServerOptions,
    serve_grpc,
)
from ledger_testing.ports import occupy_a_port, port_of

TIMEOUT: Final = 5
TOKEN: Final = "probe-token-0123456789abcdefghijklmnopq"
CALLERS: Final = CallerTokens(
    [
        CallerPolicy(
            name="probe",
            tokens=(SecretStr(TOKEN),),
            methods=frozenset(
                {
                    "/test.Echo/Say",
                    "/test.Echo/SayWithoutWaiting",
                    "/test.Stream/Repeat",
                    "/test.Stream/WriteTwice",
                    "/test.Collect/Join",
                    "/test.Relay/Pass",
                    "/test.Wait/For",
                }
            ),
        )
    ]
)
"""The test server's one caller, who may call every method it serves."""
METADATA: Final = call_metadata(SecretStr(TOKEN), call_id="test")
SECRET: Final = "card 4111111111111111"
MESSAGE: Final = "it went wrong"
GENERIC_DETAILS: Final = "internal error"
HINT_KEY: Final = "hint"

THING: Final = b"thing"
BROKEN: Final = b"broken"
BUG: Final = b"bug"
ABORT: Final = b"abort"
GONE: Final = b"gone"
CHOSEN: Final = b"chosen"
"""The method sets a status of its own and returns."""

type Context = grpc.aio.ServicerContext[bytes, bytes]
type Call = Callable[[int, bytes], Awaitable[list[bytes]]]
"""Makes one call to the test server, the kind of method that is its own, and returns what came back."""

ERRORS: Final[Mapping[ErrorKind, type[LedgerError]]] = {
    ErrorKind.INVALID: InvalidInputError,
    ErrorKind.NOT_FOUND: NotFoundError,
    ErrorKind.CONFLICT: ConflictError,
    ErrorKind.UNAUTHENTICATED: UnauthenticatedError,
    ErrorKind.FORBIDDEN: ForbiddenError,
    ErrorKind.UNAVAILABLE: UnavailableError,
    ErrorKind.INTERNAL: LedgerError,
}
EXPECTED: Final[Mapping[ErrorKind, tuple[grpc.StatusCode, str]]] = {
    ErrorKind.INVALID: (grpc.StatusCode.INVALID_ARGUMENT, "invalid_input"),
    ErrorKind.NOT_FOUND: (grpc.StatusCode.NOT_FOUND, "not_found"),
    ErrorKind.CONFLICT: (grpc.StatusCode.FAILED_PRECONDITION, "conflict"),
    ErrorKind.UNAUTHENTICATED: (grpc.StatusCode.UNAUTHENTICATED, "unauthenticated"),
    ErrorKind.FORBIDDEN: (grpc.StatusCode.PERMISSION_DENIED, "forbidden"),
    ErrorKind.UNAVAILABLE: (grpc.StatusCode.UNAVAILABLE, "unavailable"),
    ErrorKind.INTERNAL: (grpc.StatusCode.INTERNAL, "internal"),
}
BY_PAYLOAD: Final = {kind.value.encode(): error for kind, error in ERRORS.items()}
"""An internet address as a socket reports it: host and port, plus flow and scope for IPv6."""


class MissingThingError(NotFoundError):
    code: ClassVar[str] = "missing_thing"

    def __init__(self) -> None:
        super().__init__("no such thing")


class ClientGoneError(grpc.aio.BaseError):
    """One of grpc's own errors, such as the ones it raises inside a method when the client is gone."""


class BrokenInvariantError(LedgerError):
    """An error of ours: INTERNAL, with a code of its own and a message nobody outside may read."""

    code: ClassVar[str] = "broken_invariant"


async def misbehave(request: bytes, context: Context) -> None:
    """What the test server's methods do with each request: nothing, or fail the way ``request`` says."""
    if request == CHOSEN:
        context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
    if request == ABORT:
        await context.abort(grpc.StatusCode.ALREADY_EXISTS, "taken", trailing_metadata=((HINT_KEY, "retry"),))
    if request == THING:
        raise MissingThingError
    if request == BROKEN:
        raise BrokenInvariantError(SECRET)
    if request == BUG:
        raise ZeroDivisionError(SECRET)
    if request == GONE:
        raise ClientGoneError
    if request in BY_PAYLOAD:
        raise BY_PAYLOAD[request](MESSAGE)


async def say(request: bytes, context: Context) -> bytes:
    await misbehave(request, context)
    return request


async def repeat(request: bytes, context: Context) -> AsyncGenerator[bytes]:
    """One item goes out before anything fails: the headers are on the wire by then."""
    yield request
    await misbehave(request, context)
    yield request


async def write_twice(request: bytes, context: Context) -> None:
    """A streaming method written the other way: a coroutine that writes to the context."""
    await misbehave(request, context)
    await context.write(request)
    await context.write(request)


async def join(requests: AsyncIterator[bytes], context: Context) -> bytes:
    joined = b""
    async for request in requests:
        await misbehave(request, context)
        joined += request
    return joined


async def relay(requests: AsyncIterator[bytes], context: Context) -> AsyncGenerator[bytes]:
    async for request in requests:
        await misbehave(request, context)
        yield request


def say_without_waiting(request: bytes, context: grpc.ServicerContext) -> bytes:
    """A synchronous method: grpc runs it on a thread."""
    del context
    return request


def serve_methods(
    server: grpc.aio.Server, service: str, **methods: grpc.RpcMethodHandler[bytes, bytes]
) -> None:
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(service, methods),))


def register(server: grpc.aio.Server) -> None:
    """Every kind of method, plus the two shapes of a streaming response, plus a synchronous one."""
    serve_methods(
        server,
        "test.Echo",
        Say=grpc.unary_unary_rpc_method_handler(say, request_deserializer=bytes, response_serializer=bytes),
        SayWithoutWaiting=grpc.unary_unary_rpc_method_handler(
            say_without_waiting, request_deserializer=bytes, response_serializer=bytes
        ),
    )
    serve_methods(
        server,
        "test.Stream",
        Repeat=grpc.unary_stream_rpc_method_handler(
            repeat, request_deserializer=bytes, response_serializer=bytes
        ),
        WriteTwice=grpc.unary_stream_rpc_method_handler(
            write_twice, request_deserializer=bytes, response_serializer=bytes
        ),
    )
    serve_methods(
        server,
        "test.Collect",
        Join=grpc.stream_unary_rpc_method_handler(
            join, request_deserializer=bytes, response_serializer=bytes
        ),
    )
    serve_methods(
        server,
        "test.Relay",
        Pass=grpc.stream_stream_rpc_method_handler(
            relay, request_deserializer=bytes, response_serializer=bytes
        ),
    )


def channel_to(port: int) -> grpc.aio.Channel:
    return grpc.aio.insecure_channel(f"127.0.0.1:{port}")


async def call_named(port: int, method: str) -> bytes:
    async with channel_to(port) as channel:
        return await channel.unary_unary(method, request_serializer=bytes, response_deserializer=bytes)(
            b"x", metadata=METADATA, timeout=TIMEOUT
        )


async def call_unary(port: int, payload: bytes) -> list[bytes]:
    async with channel_to(port) as channel:
        method = channel.unary_unary("/test.Echo/Say", request_serializer=bytes, response_deserializer=bytes)
        return [await method(payload, metadata=METADATA, timeout=TIMEOUT)]


async def call_unary_stream(port: int, payload: bytes) -> list[bytes]:
    async with channel_to(port) as channel:
        method = channel.unary_stream(
            "/test.Stream/Repeat", request_serializer=bytes, response_deserializer=bytes
        )
        return [item async for item in method(payload, metadata=METADATA, timeout=TIMEOUT)]


async def call_unary_stream_written(port: int, payload: bytes) -> list[bytes]:
    async with channel_to(port) as channel:
        method = channel.unary_stream(
            "/test.Stream/WriteTwice", request_serializer=bytes, response_deserializer=bytes
        )
        return [item async for item in method(payload, metadata=METADATA, timeout=TIMEOUT)]


async def call_stream_unary(port: int, payload: bytes) -> list[bytes]:
    async with channel_to(port) as channel:
        method = channel.stream_unary(
            "/test.Collect/Join", request_serializer=bytes, response_deserializer=bytes
        )
        return [await method(iter([payload]), metadata=METADATA, timeout=TIMEOUT)]


async def call_stream_stream(port: int, payload: bytes) -> list[bytes]:
    async with channel_to(port) as channel:
        method = channel.stream_stream(
            "/test.Relay/Pass", request_serializer=bytes, response_deserializer=bytes
        )
        return [item async for item in method(iter([payload]), metadata=METADATA, timeout=TIMEOUT)]


CALLS: Final[Mapping[str, Call]] = {
    "unary-unary": call_unary,
    "unary-stream": call_unary_stream,
    "unary-stream-written": call_unary_stream_written,
    "stream-unary": call_stream_unary,
    "stream-stream": call_stream_stream,
}
EVERY_CALL = pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())


@pytest.fixture
async def port(registry: CollectorRegistry) -> AsyncGenerator[int]:
    async with serve_grpc(port=0, register=register, registry=registry, callers=CALLERS) as bound:
        yield bound


def trailers(error: grpc.aio.AioRpcError) -> dict[str, str | bytes]:
    return dict(error.trailing_metadata() or ())


async def failure_of(call: Call, port: int, payload: bytes) -> grpc.aio.AioRpcError:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call(port, payload)
    return caught.value


async def test_a_call_succeeds(port: int) -> None:
    assert await call_unary(port, b"hello") == [b"hello"]


@pytest.mark.parametrize(
    ("call", "replies"),
    [
        (call_unary_stream, [b"x", b"x"]),
        (call_unary_stream_written, [b"x", b"x"]),
        (call_stream_unary, [b"x"]),
        (call_stream_stream, [b"x"]),
    ],
    ids=["unary-stream", "unary-stream-written", "stream-unary", "stream-stream"],
)
async def test_a_streaming_call_succeeds(call: Call, replies: list[bytes], port: int) -> None:
    assert await call(port, b"x") == replies


async def test_a_ledger_error_becomes_a_status_with_its_code(port: int) -> None:
    error = await failure_of(call_unary, port, THING)
    assert (error.code(), error.details()) == (grpc.StatusCode.NOT_FOUND, "no such thing")
    assert (ERROR_CODE_KEY, "missing_thing") in tuple(error.trailing_metadata() or ())


def test_every_kind_of_error_is_expected_to_have_a_status() -> None:
    assert EXPECTED.keys() == set(ErrorKind)


@EVERY_CALL
@pytest.mark.parametrize("kind", list(ErrorKind))
async def test_each_kind_has_its_status_in_every_kind_of_method(
    call: Call, kind: ErrorKind, port: int
) -> None:
    status, code = EXPECTED[kind]
    with capture_logs() as logs:
        error = await failure_of(call, port, kind.value.encode())
    assert (error.code(), trailers(error)[ERROR_CODE_KEY]) == (status, code)
    assert error.details() == (GENERIC_DETAILS if kind is ErrorKind.INTERNAL else MESSAGE)
    assert len(logs) == (kind is ErrorKind.INTERNAL)  # only a defect of ours is logged


@EVERY_CALL
@pytest.mark.parametrize("payload", [BUG, BROKEN])
async def test_a_defect_tells_the_client_nothing_but_internal(call: Call, payload: bytes, port: int) -> None:
    """grpc's own answer to a bug is ``UNKNOWN`` with the text of the exception."""
    with capture_logs():
        error = await failure_of(call, port, payload)
    assert (error.code(), error.details(), trailers(error)[ERROR_CODE_KEY]) == (
        grpc.StatusCode.INTERNAL,
        GENERIC_DETAILS,
        "internal",
    )
    assert SECRET not in str(error)


async def test_a_bug_is_logged_where_it_happened_and_what_it_was(port: int) -> None:
    with capture_logs() as logs:
        await failure_of(call_unary, port, BUG)
    (entry,) = logs
    assert isinstance(entry.pop("exc_info"), ZeroDivisionError)
    assert entry == {
        "event": "grpc.unhandled_error",
        "log_level": "error",
        "rpc": "/test.Echo/Say",
        "code": "internal",
    }


async def test_an_internal_error_answers_like_a_bug_and_keeps_its_code_for_the_log(port: int) -> None:
    with capture_logs() as logs:
        await failure_of(call_unary_stream, port, BROKEN)
    (entry,) = logs
    assert isinstance(entry.pop("exc_info"), BrokenInvariantError)
    assert entry == {
        "event": "grpc.unhandled_error",
        "log_level": "error",
        "rpc": "/test.Stream/Repeat",
        "code": "broken_invariant",
    }


@EVERY_CALL
async def test_a_method_that_aborts_keeps_its_own_status(call: Call, port: int) -> None:
    with capture_logs() as logs:
        error = await failure_of(call, port, ABORT)
    assert (error.code(), error.details()) == (grpc.StatusCode.ALREADY_EXISTS, "taken")
    assert trailers(error) == {HINT_KEY: "retry"}
    assert logs == []


@EVERY_CALL
async def test_an_error_of_grpc_itself_is_neither_answered_nor_logged_as_our_defect(
    call: Call, port: int
) -> None:
    with capture_logs() as logs:
        error = await failure_of(call, port, GONE)
    assert ERROR_CODE_KEY not in trailers(error)
    assert error.details() != GENERIC_DETAILS
    assert logs == []


async def test_the_error_of_a_stream_arrives_after_the_items_before_it(port: int) -> None:
    async with channel_to(port) as channel:
        method = channel.unary_stream(
            "/test.Stream/Repeat", request_serializer=bytes, response_deserializer=bytes
        )
        stream = method(THING, metadata=METADATA, timeout=TIMEOUT)
        assert await stream.read() == THING
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await stream.read()
    assert (caught.value.code(), caught.value.details()) == (grpc.StatusCode.NOT_FOUND, "no such thing")


async def test_a_synchronous_method_is_never_served(port: int, registry: CollectorRegistry) -> None:
    """``grpc.aio`` would run it on a thread, outside the caller guard: it fails as a defect of ours."""
    with capture_logs() as logs, pytest.raises(grpc.aio.AioRpcError) as caught:
        await call_named(port, "/test.Echo/SayWithoutWaiting")
    assert (caught.value.code(), caught.value.details(), trailers(caught.value)[ERROR_CODE_KEY]) == (
        grpc.StatusCode.INTERNAL,
        GENERIC_DETAILS,
        "internal",
    )
    assert [(entry["event"], entry["code"]) for entry in logs] == [
        ("grpc.unhandled_error", "grpc_method_synchronous")
    ]
    assert duration_count(registry, "/test.Echo/SayWithoutWaiting", "INTERNAL") == 1


@dataclass(slots=True)
class Details:
    """What grpc tells an interceptor about the call it is about to route."""

    method: str
    invocation_metadata: tuple[tuple[str, str | bytes], ...] = ()


async def test_a_synchronous_method_is_left_untouched() -> None:
    """It runs on a thread, with another kind of context: the interceptor answers for asynchronous ones."""
    handler: grpc.RpcMethodHandler[bytes, bytes] = grpc.unary_unary_rpc_method_handler(
        say_without_waiting, request_deserializer=bytes, response_serializer=bytes
    )

    async def continuation(details: grpc.HandlerCallDetails) -> grpc.RpcMethodHandler[bytes, bytes] | None:
        del details
        return handler

    guarded = await GuardInterceptor(CollectorRegistry()).intercept_service(
        continuation, Details("/test.Echo/SayWithoutWaiting")
    )
    assert guarded is handler


async def test_unknown_methods_are_unimplemented(port: int) -> None:
    async with channel_to(port) as channel:
        missing = channel.unary_unary(
            "/test.Echo/Nothing", request_serializer=bytes, response_deserializer=bytes
        )
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await missing(b"", metadata=METADATA, timeout=TIMEOUT)
    assert caught.value.code() == grpc.StatusCode.UNIMPLEMENTED


def register_waiting(
    started: asyncio.Event, release: asyncio.Event, cancelled: asyncio.Event
) -> Callable[[grpc.aio.Server], None]:
    """A server whose one method says it began, then waits to be released (or cancelled)."""

    async def wait(request: bytes, context: Context) -> bytes:
        del context
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return request

    def register_it(server: grpc.aio.Server) -> None:
        serve_methods(
            server,
            "test.Wait",
            For=grpc.unary_unary_rpc_method_handler(
                wait, request_deserializer=bytes, response_serializer=bytes
            ),
        )

    return register_it


async def call_waiting(port: int) -> bytes:
    async with channel_to(port) as channel:
        method = channel.unary_unary("/test.Wait/For", request_serializer=bytes, response_deserializer=bytes)
        return await method(b"done", metadata=METADATA, timeout=TIMEOUT)


async def test_a_call_in_flight_finishes_within_the_grace(registry: CollectorRegistry) -> None:
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with serve_grpc(
        port=0,
        register=register_waiting(started, release, cancelled),
        registry=registry,
        callers=CALLERS,
        options=ServerOptions(grace=TIMEOUT),
    ) as bound:
        call = asyncio.create_task(call_waiting(bound))
        async with asyncio.timeout(TIMEOUT):
            await started.wait()
        asyncio.get_running_loop().call_later(0.1, release.set)  # the server is already stopping by then
    assert await call == b"done"
    assert not cancelled.is_set()


async def test_a_call_that_outlives_the_grace_is_cancelled_and_is_no_bug(registry: CollectorRegistry) -> None:
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    with capture_logs() as logs:
        async with serve_grpc(
            port=0,
            register=register_waiting(started, release, cancelled),
            registry=registry,
            callers=CALLERS,
            options=ServerOptions(grace=0.1),
        ) as bound:
            call = asyncio.create_task(call_waiting(bound))
            async with asyncio.timeout(TIMEOUT):
                await started.wait()
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call
    assert caught.value.code() == grpc.StatusCode.UNAVAILABLE
    assert cancelled.is_set()
    assert logs == []


@given(number=st.one_of(st.integers(max_value=-1), st.integers(min_value=65536)))
@example(number=-1)
@example(number=65536)
def test_a_port_outside_the_range_is_refused_at_once(number: int) -> None:
    with pytest.raises(InvalidOptionError, match=r"^gRPC option port must be between 0 and 65535, not "):
        serve_grpc(port=number, register=register, registry=CollectorRegistry(), callers=CALLERS)


@given(seconds=st.floats().filter(lambda value: not (value > 0 and isfinite(value))))
@example(seconds=0.0)
@example(seconds=inf)
@example(seconds=nan)
def test_a_grace_that_is_not_a_positive_finite_time_is_refused_at_once(seconds: float) -> None:
    with pytest.raises(InvalidOptionError, match=r"^gRPC option grace must be a positive, finite number "):
        ServerOptions(grace=seconds)


@given(
    number=st.integers(min_value=0, max_value=65535),
    seconds=st.floats(min_value=0, exclude_min=True, allow_infinity=False),
)
@example(number=0, seconds=5e-324)
@example(number=65535, seconds=1e300)
def test_every_port_and_every_positive_finite_grace_is_accepted(number: int, seconds: float) -> None:
    """Accepted as options: nothing is bound until the server is entered."""
    serve_grpc(
        port=number,
        register=register,
        registry=CollectorRegistry(),
        callers=CALLERS,
        options=ServerOptions(grace=seconds),
    )


class StartFailedError(Exception):
    def __init__(self) -> None:
        super().__init__("the server would not start")


async def test_a_server_that_cannot_start_is_stopped(
    registry: CollectorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bound and registered, then ``start`` raises: nothing may be left listening."""
    servers: list[grpc.aio.Server] = []
    stopped: list[float | None] = []
    make = grpc.aio.server

    def failing_server(
        *,
        interceptors: Sequence[grpc.aio.ServerInterceptor],
        maximum_concurrent_rpcs: int,
        options: Sequence[tuple[str, int]],
    ) -> grpc.aio.Server:
        server = make(
            interceptors=interceptors, maximum_concurrent_rpcs=maximum_concurrent_rpcs, options=options
        )
        real_stop = server.stop

        async def start() -> None:
            raise StartFailedError

        async def stop(grace: float | None) -> None:
            stopped.append(grace)
            await real_stop(grace)

        monkeypatch.setattr(server, "start", start)
        monkeypatch.setattr(server, "stop", stop)
        servers.append(server)
        return server

    monkeypatch.setattr(grpc.aio, "server", failing_server)
    with pytest.raises(StartFailedError):
        async with serve_grpc(
            port=0, register=register, registry=registry, callers=CALLERS, options=ServerOptions(grace=0.5)
        ):
            pass
    assert len(servers) == 1
    assert stopped == [0.5]


async def test_a_port_that_is_taken_is_a_startup_error() -> None:
    with occupy_a_port() as taken:
        number = port_of(taken)
        with pytest.raises(GrpcBindError, match=rf"^the gRPC server could not bind port {number}$") as caught:
            async with serve_grpc(
                port=number, register=register, registry=CollectorRegistry(), callers=CALLERS
            ):
                pass
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.code == "grpc_bind_failed"


def duration_count(registry: CollectorRegistry, rpc: str, code: str) -> float | None:
    return registry.get_sample_value("ledger_grpc_request_duration_seconds_count", {"rpc": rpc, "code": code})


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"hello", "OK"),
        (THING, "NOT_FOUND"),
        (BUG, "INTERNAL"),
        (BROKEN, "INTERNAL"),
        (ABORT, "ALREADY_EXISTS"),
        (GONE, "UNKNOWN"),
    ],
    ids=["answered", "ledger-error", "bug", "internal-error", "aborted-by-the-method", "grpc-error"],
)
async def test_every_call_is_timed_by_method_and_status(
    port: int, registry: CollectorRegistry, payload: bytes, code: str
) -> None:
    with capture_logs(), contextlib.suppress(grpc.aio.AioRpcError):
        await call_unary(port, payload)
    assert duration_count(registry, "/test.Echo/Say", code) == 1


async def test_a_streaming_call_is_timed_whole(port: int, registry: CollectorRegistry) -> None:
    assert await call_unary_stream(port, b"x") == [b"x", b"x"]
    assert duration_count(registry, "/test.Stream/Repeat", "OK") == 1


async def test_a_status_the_method_set_itself_is_the_one_measured(
    port: int, registry: CollectorRegistry
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call_unary(port, CHOSEN)
    assert caught.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert duration_count(registry, "/test.Echo/Say", "RESOURCE_EXHAUSTED") == 1


async def test_calls_of_a_method_nobody_registered_are_not_timed(
    port: int, registry: CollectorRegistry
) -> None:
    """An unknown method is the client's to name: each name would be a series."""
    async with channel_to(port) as channel:
        with contextlib.suppress(grpc.aio.AioRpcError):
            await channel.unary_unary(
                "/test.Echo/Nothing", request_serializer=bytes, response_deserializer=bytes
            )(b"", metadata=METADATA, timeout=TIMEOUT)
    measured = [
        sample.labels
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == "ledger_grpc_request_duration_seconds_count"
    ]
    assert measured == []


async def test_a_call_cancelled_at_the_end_of_the_grace_is_timed_as_cancelled(
    registry: CollectorRegistry,
) -> None:
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with serve_grpc(
        port=0,
        register=register_waiting(started, release, cancelled),
        registry=registry,
        callers=CALLERS,
        options=ServerOptions(grace=0.1),
    ) as bound:
        call = asyncio.create_task(call_waiting(bound))
        async with asyncio.timeout(TIMEOUT):
            await started.wait()
    with pytest.raises(grpc.aio.AioRpcError):
        await call
    assert duration_count(registry, "/test.Wait/For", "CANCELLED") == 1


@pytest.mark.parametrize("limit", [0, -1, MAX_MESSAGE_BYTES + 1], ids=["zero", "negative", "over-the-most"])
def test_a_message_limit_outside_its_range_is_refused_at_once(limit: int) -> None:
    with pytest.raises(
        InvalidOptionError,
        match=rf"^gRPC option max_message_bytes must be 1 to {MAX_MESSAGE_BYTES} bytes, not ",
    ):
        ServerOptions(max_message_bytes=limit)


@pytest.mark.parametrize("limit", [1, MAX_MESSAGE_BYTES], ids=["least", "most"])
def test_a_message_limit_within_its_range_is_accepted(limit: int) -> None:
    assert ServerOptions(max_message_bytes=limit).max_message_bytes == limit


def refuse_to_read(raw: bytes) -> bytes:
    """A deserializer that cannot read what it is given, and says so quoting it."""
    raise ValueError(raw.decode())


@pytest.fixture
async def unguarded_port(registry: CollectorRegistry) -> AsyncGenerator[int]:
    """A server with ``GuardInterceptor`` alone, whose methods cannot read their requests."""
    server = grpc.aio.server(interceptors=(GuardInterceptor(registry),))
    serve_methods(
        server,
        "test.Echo",
        Say=grpc.unary_unary_rpc_method_handler(
            say, request_deserializer=refuse_to_read, response_serializer=bytes
        ),
    )
    serve_methods(
        server,
        "test.Collect",
        Join=grpc.stream_unary_rpc_method_handler(
            join, request_deserializer=refuse_to_read, response_serializer=bytes
        ),
    )
    bound = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield bound
    finally:
        await server.stop(None)


@pytest.mark.parametrize("call", [call_unary, call_stream_unary], ids=["unary-unary", "stream-unary"])
async def test_a_body_that_cannot_be_read_is_invalid_even_without_the_caller_guard(
    call: Call, unguarded_port: int, registry: CollectorRegistry
) -> None:
    """``GuardInterceptor`` reads the request itself: grpc would answer ``UNKNOWN`` with the exception."""
    with capture_logs() as logs:
        error = await failure_of(call, unguarded_port, SECRET.encode())
    assert (error.code(), error.details(), trailers(error)[ERROR_CODE_KEY]) == (
        grpc.StatusCode.INVALID_ARGUMENT,
        "the request could not be read",
        "grpc_request_malformed",
    )
    assert SECRET not in str(error)
    assert logs == []
    measured = [
        sample.labels["code"]
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == "ledger_grpc_request_duration_seconds_count"
    ]
    assert measured == ["INVALID_ARGUMENT"]


@pytest.mark.parametrize("limit", [0, -1, MAX_CONCURRENT_RPCS + 1], ids=["zero", "negative", "over-the-most"])
def test_a_concurrency_limit_outside_its_range_is_refused_at_once(limit: int) -> None:
    with pytest.raises(
        InvalidOptionError,
        match=rf"^gRPC option max_concurrent_rpcs must be 1 to {MAX_CONCURRENT_RPCS} calls, not ",
    ):
        ServerOptions(max_concurrent_rpcs=limit)


def test_a_server_takes_a_bounded_number_of_calls_at_once_by_default() -> None:
    assert 1 <= ServerOptions().max_concurrent_rpcs == DEFAULT_MAX_CONCURRENT_RPCS <= MAX_CONCURRENT_RPCS


async def test_a_call_past_the_concurrency_limit_is_refused_resource_exhausted(
    registry: CollectorRegistry,
) -> None:
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with serve_grpc(
        port=0,
        register=register_waiting(started, release, cancelled),
        registry=registry,
        callers=CALLERS,
        options=ServerOptions(grace=0.1, max_concurrent_rpcs=1),
    ) as bound:
        first = asyncio.create_task(call_waiting(bound))
        async with asyncio.timeout(TIMEOUT):
            await started.wait()
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call_waiting(bound)
        release.set()
        assert await first == b"done"
    assert caught.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
