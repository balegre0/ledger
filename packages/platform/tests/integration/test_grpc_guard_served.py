"""The guard on a real server: a tiny generic service on port 0, no protos needed."""

from typing import Final, final
lazy from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping

import grpc
import pytest
import structlog
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import SecretStr
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
lazy from prometheus_client import CollectorRegistry

from ledger_platform.grpc.client import ERROR_CODE_KEY
from ledger_platform.grpc.guard import CallerPolicy, CallerTokens, current_caller
from ledger_platform.grpc.metadata import call_metadata
from ledger_platform.grpc.server import ServerOptions, serve_grpc
lazy from ledger_platform.observability.tracing import Tracing

TIMEOUT: Final = 5
API_TOKEN: Final = "api-token-0123456789abcdefghijklmnopqrstuv"
WORKER_TOKEN: Final = "worker-token-0123456789abcdefghijklmnopq"
UNKNOWN_TOKEN: Final = "unknown-token-0123456789abcdefghijklmnop"
SERVICE: Final = "test.Guarded"
SIGN: Final = f"/{SERVICE}/Sign"
IMPORT: Final = f"/{SERVICE}/Import"
WATCH: Final = f"/{SERVICE}/Watch"
UPLOAD: Final = f"/{SERVICE}/Upload"
RELAY: Final = f"/{SERVICE}/Relay"
BLOCKING: Final = f"/{SERVICE}/Blocking"
LIMIT: Final = 1024
"""The largest message the test server takes or sends."""

type Metadata = tuple[tuple[str, str], ...]
type Context = grpc.aio.ServicerContext[bytes, bytes]
type Call = Callable[[grpc.aio.Channel, Metadata], Awaitable[list[bytes]]]
"""Calls one of the test methods with ``metadata``; what came back."""


@final
class Ran:
    """How many times a method of the test service began: a guard refuses before any does."""

    def __init__(self) -> None:
        self.count = 0


def register_with(ran: Ran) -> Callable[[grpc.aio.Server], None]:
    """Methods of every kind; each counts its run and answers with its caller's name."""

    async def sign(request: bytes, context: Context) -> bytes:
        del request, context
        ran.count += 1
        structlog.stdlib.get_logger("test").info("test.signing")
        return current_caller().encode()

    async def watch(request: bytes, context: Context) -> AsyncGenerator[bytes]:
        del request, context
        ran.count += 1
        yield current_caller().encode()

    async def upload(requests: AsyncIterator[bytes], context: Context) -> bytes:
        del context
        ran.count += 1
        return b"".join([request async for request in requests]) + current_caller().encode()

    async def relay(requests: AsyncIterator[bytes], context: Context) -> AsyncGenerator[bytes]:
        del context
        ran.count += 1
        async for request in requests:
            yield request + current_caller().encode()

    def blocking(request: bytes, context: grpc.ServicerContext) -> bytes:
        del context
        ran.count += 1
        return request

    def register(server: grpc.aio.Server) -> None:
        handlers: dict[str, grpc.RpcMethodHandler[bytes, bytes]] = {
            "Sign": grpc.unary_unary_rpc_method_handler(
                sign, request_deserializer=bytes, response_serializer=bytes
            ),
            "Import": grpc.unary_unary_rpc_method_handler(
                sign, request_deserializer=bytes, response_serializer=bytes
            ),
            "Watch": grpc.unary_stream_rpc_method_handler(
                watch, request_deserializer=bytes, response_serializer=bytes
            ),
            "Upload": grpc.stream_unary_rpc_method_handler(
                upload, request_deserializer=bytes, response_serializer=bytes
            ),
            "Relay": grpc.stream_stream_rpc_method_handler(
                relay, request_deserializer=bytes, response_serializer=bytes
            ),
            "Blocking": grpc.unary_unary_rpc_method_handler(
                blocking, request_deserializer=bytes, response_serializer=bytes
            ),
        }
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE, handlers),))

    return register


def callers() -> CallerTokens:
    return CallerTokens(
        [
            CallerPolicy(name="api", tokens=(SecretStr(API_TOKEN),), methods=frozenset({IMPORT})),
            CallerPolicy(
                name="worker",
                tokens=(SecretStr(WORKER_TOKEN),),
                methods=frozenset({SIGN, WATCH, UPLOAD, RELAY, BLOCKING}),
            ),
        ]
    )


@pytest.fixture
def ran() -> Ran:
    return Ran()


@pytest.fixture
async def channel(
    ran: Ran, registry: CollectorRegistry, tracing: Tracing
) -> AsyncGenerator[grpc.aio.Channel]:
    async with (
        serve_grpc(
            port=0,
            register=register_with(ran),
            registry=registry,
            callers=callers(),
            options=ServerOptions(tracer=tracing.tracer, max_message_bytes=LIMIT),
        ) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as opened,
    ):
        yield opened


def as_worker(call_id: str = "req_1") -> Metadata:
    return call_metadata(SecretStr(WORKER_TOKEN), call_id=call_id)


def as_api() -> Metadata:
    return call_metadata(SecretStr(API_TOKEN), call_id="req_1")


async def call_sign(channel: grpc.aio.Channel, metadata: Metadata) -> list[bytes]:
    method = channel.unary_unary(SIGN, request_serializer=bytes, response_deserializer=bytes)
    return [await method(b"doc", metadata=metadata, timeout=TIMEOUT)]


async def call_watch(channel: grpc.aio.Channel, metadata: Metadata) -> list[bytes]:
    method = channel.unary_stream(WATCH, request_serializer=bytes, response_deserializer=bytes)
    return [item async for item in method(b"doc", metadata=metadata, timeout=TIMEOUT)]


async def call_upload(channel: grpc.aio.Channel, metadata: Metadata) -> list[bytes]:
    method = channel.stream_unary(UPLOAD, request_serializer=bytes, response_deserializer=bytes)
    return [await method(iter([b"a", b"b"]), metadata=metadata, timeout=TIMEOUT)]


async def call_relay(channel: grpc.aio.Channel, metadata: Metadata) -> list[bytes]:
    method = channel.stream_stream(RELAY, request_serializer=bytes, response_deserializer=bytes)
    return [item async for item in method(iter([b"a"]), metadata=metadata, timeout=TIMEOUT)]


CALLS: Final[Mapping[str, Call]] = {
    "unary-unary": call_sign,
    "unary-stream": call_watch,
    "stream-unary": call_upload,
    "stream-stream": call_relay,
}
EVERY_KIND = pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())


async def failure(call: Call, channel: grpc.aio.Channel, metadata: Metadata) -> grpc.aio.AioRpcError:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call(channel, metadata)
    return caught.value


def code_of(error: grpc.aio.AioRpcError) -> str | bytes | None:
    return dict(error.trailing_metadata() or ()).get(ERROR_CODE_KEY)


def duration_count(registry: CollectorRegistry, rpc: str, code: str) -> float | None:
    return registry.get_sample_value("ledger_grpc_request_duration_seconds_count", {"rpc": rpc, "code": code})


NOBODY: Final[Mapping[str, Metadata]] = {
    "no-metadata": (),
    "no-authorization": (("ledger-call-id", "req_1"),),
    "malformed": (("authorization", WORKER_TOKEN),),
    "unknown-token": (("authorization", f"Bearer {UNKNOWN_TOKEN}"),),
    "twice": (("authorization", f"Bearer {WORKER_TOKEN}"), ("authorization", f"Bearer {WORKER_TOKEN}")),
}


@EVERY_KIND
@pytest.mark.parametrize("metadata", NOBODY.values(), ids=NOBODY.keys())
async def test_no_token_is_unauthenticated_before_the_method_runs(
    call: Call, metadata: Metadata, channel: grpc.aio.Channel, ran: Ran
) -> None:
    with capture_logs():
        error = await failure(call, channel, metadata)
    assert (error.code(), error.details(), code_of(error)) == (
        grpc.StatusCode.UNAUTHENTICATED,
        "the caller is not known",
        "signer_caller_unknown",
    )
    assert ran.count == 0


async def test_a_method_outside_the_allowlist_is_permission_denied(
    channel: grpc.aio.Channel, ran: Ran, registry: CollectorRegistry
) -> None:
    with capture_logs():
        error = await failure(call_sign, channel, as_api())
    assert (error.code(), error.details(), code_of(error)) == (
        grpc.StatusCode.PERMISSION_DENIED,
        "the caller may not call this method",
        "signer_method_not_allowed",
    )
    assert ran.count == 0
    assert duration_count(registry, SIGN, "PERMISSION_DENIED") == 1


async def test_refusals_are_timed_like_any_call(
    channel: grpc.aio.Channel, registry: CollectorRegistry
) -> None:
    with capture_logs():
        await failure(call_sign, channel, ())
    assert duration_count(registry, SIGN, "UNAUTHENTICATED") == 1


async def test_refusals_are_logged_by_rpc_and_code_never_with_the_token(channel: grpc.aio.Channel) -> None:
    with capture_logs() as logs:
        await failure(call_sign, channel, (("authorization", f"Bearer {UNKNOWN_TOKEN}"),))
        await failure(call_sign, channel, as_api())
    assert logs == [
        {
            "event": "grpc.caller_refused",
            "log_level": "warning",
            "rpc": SIGN,
            "code": "signer_caller_unknown",
        },
        {
            "event": "grpc.caller_refused",
            "log_level": "warning",
            "rpc": SIGN,
            "code": "signer_method_not_allowed",
            "caller": "api",
        },
    ]
    assert UNKNOWN_TOKEN not in repr(logs)
    assert API_TOKEN not in repr(logs)


@EVERY_KIND
async def test_the_caller_is_bound_inside_the_method(call: Call, channel: grpc.aio.Channel, ran: Ran) -> None:
    replies = await call(channel, as_worker())
    assert replies[-1].endswith(b"worker")
    assert ran.count == 1


async def test_the_allowed_caller_reaches_its_method(channel: grpc.aio.Channel) -> None:
    method = channel.unary_unary(IMPORT, request_serializer=bytes, response_deserializer=bytes)
    assert await method(b"doc", metadata=as_api(), timeout=TIMEOUT) == b"api"


async def test_the_method_s_logs_name_the_rpc_the_caller_and_the_call(channel: grpc.aio.Channel) -> None:
    structlog.contextvars.clear_contextvars()
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await call_sign(channel, as_worker("req_42"))
        await call_sign(channel, as_worker("not a call id"))
    assert logs == [
        {"event": "test.signing", "log_level": "info", "rpc": SIGN, "caller": "worker", "call_id": "req_42"},
        {"event": "test.signing", "log_level": "info", "rpc": SIGN, "caller": "worker"},
    ]


async def test_a_synchronous_method_is_refused_even_to_its_caller(
    channel: grpc.aio.Channel, ran: Ran, registry: CollectorRegistry
) -> None:
    """``grpc.aio`` would run it on a thread, outside the guard: a defect of the service, never served."""
    method = channel.unary_unary(BLOCKING, request_serializer=bytes, response_deserializer=bytes)
    with capture_logs() as logs, pytest.raises(grpc.aio.AioRpcError) as caught:
        await method(b"doc", metadata=as_worker(), timeout=TIMEOUT)
    assert (caught.value.code(), code_of(caught.value)) == (grpc.StatusCode.INTERNAL, "internal")
    assert ran.count == 0
    assert [(entry["event"], entry["code"]) for entry in logs] == [
        ("grpc.unhandled_error", "grpc_method_synchronous")
    ]
    assert duration_count(registry, BLOCKING, "INTERNAL") == 1


async def test_a_synchronous_method_still_refuses_an_unknown_caller_first(channel: grpc.aio.Channel) -> None:
    method = channel.unary_unary(BLOCKING, request_serializer=bytes, response_deserializer=bytes)
    with capture_logs(), pytest.raises(grpc.aio.AioRpcError) as caught:
        await method(b"doc", timeout=TIMEOUT)
    assert caught.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_an_unregistered_method_is_unimplemented_and_untimed(
    channel: grpc.aio.Channel, registry: CollectorRegistry
) -> None:
    missing = channel.unary_unary(
        f"/{SERVICE}/Nothing", request_serializer=bytes, response_deserializer=bytes
    )
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await missing(b"", timeout=TIMEOUT)
    assert caught.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert duration_count(registry, f"/{SERVICE}/Nothing", "UNAUTHENTICATED") is None


async def test_messages_over_the_limit_are_refused(channel: grpc.aio.Channel, ran: Ran) -> None:
    method = channel.unary_unary(SIGN, request_serializer=bytes, response_deserializer=bytes)
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await method(b"x" * (LIMIT + 1), metadata=as_worker(), timeout=TIMEOUT)
    assert caught.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert ran.count == 0


async def test_the_trace_continues_from_the_caller(
    channel: grpc.aio.Channel, tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    with tracing.tracer.start_as_current_span("caller") as caller:
        metadata = as_worker()
    await call_sign(channel, metadata)
    (served,) = [span for span in exporter.get_finished_spans() if span.name == SIGN]
    assert served.kind is SpanKind.SERVER
    assert served.parent is not None
    assert served.parent.span_id == caller.get_span_context().span_id
    assert served.context is not None
    assert served.context.trace_id == caller.get_span_context().trace_id
    assert dict(served.attributes or {}) == {"ledger.rpc": SIGN, "ledger.caller": "worker"}
    assert served.status.status_code is StatusCode.UNSET


async def test_a_call_without_a_trace_starts_one(
    channel: grpc.aio.Channel, exporter: InMemorySpanExporter
) -> None:
    oversized = "00-" + "1" * 32 + "-" + "2" * 16 + "-01" + " " * 100
    await call_sign(channel, (*as_worker(), ("traceparent", oversized)))
    await call_sign(channel, as_worker())
    spans = [span for span in exporter.get_finished_spans() if span.name == SIGN]
    assert len(spans) == 2
    assert all(span.parent is None for span in spans)


async def test_a_refused_call_is_traced_as_failed_without_a_caller(
    channel: grpc.aio.Channel, exporter: InMemorySpanExporter
) -> None:
    with capture_logs():
        await failure(call_sign, channel, ())
    (served,) = [span for span in exporter.get_finished_spans() if span.name == SIGN]
    assert dict(served.attributes or {}) == {"ledger.rpc": SIGN}
    assert served.status.status_code is StatusCode.ERROR
    assert [(event.name, dict(event.attributes or {})) for event in served.events] == [
        ("exception", {"exception.type": "ledger_platform.grpc.guard.CallerUnknownError"})
    ]
