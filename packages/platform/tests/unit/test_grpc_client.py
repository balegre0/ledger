import asyncio
import math
from typing import ClassVar, Final
lazy from collections.abc import AsyncGenerator

import grpc
import pytest
from prometheus_client import CollectorRegistry

from ledger_kernel.errors import ErrorKind, NotFoundError, truncate
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.grpc.client import (
    ERROR_CODE_KEY,
    MAX_DEADLINE,
    REMOTE_CODE_LIMIT,
    RemoteConflictError,
    RemoteDefectError,
    RemoteError,
    RemoteForbiddenError,
    RemoteNotFoundError,
    RemoteUnavailableError,
    call_unary,
)
from ledger_platform.grpc.server import serve_grpc

RPC: Final = "/test.Remote/Call"
LONG_CODE: Final = "x" * 1_000
"""A code no chassis server writes, but another side could (within gRPC's 8 KiB of metadata)."""
ODD_CODE: Final = "Bad Code! <b>"
"""Text that is not a stable code: it never reaches a message a client may read."""
type Context = grpc.aio.ServicerContext[bytes, bytes]


class MissingThingError(NotFoundError):
    code: ClassVar[str] = "missing_thing"

    def __init__(self) -> None:
        super().__init__("no thing 4111111111111111")


async def behave(request: bytes, context: Context) -> bytes:
    """Answer as the request says: echo it, fail with a status by name, fail as a ledger error, or stall."""
    text = request.decode()
    if text == "missing":
        raise MissingThingError
    if text == "slow":
        await asyncio.sleep(5)
    if text == "bare":
        await context.abort(grpc.StatusCode.NOT_FOUND, "no code")
    if text == "odd":
        await context.abort(grpc.StatusCode.NOT_FOUND, "odd", trailing_metadata=((ERROR_CODE_KEY, ODD_CODE),))
    if text == "long":
        await context.abort(
            grpc.StatusCode.NOT_FOUND, "long", trailing_metadata=((ERROR_CODE_KEY, LONG_CODE),)
        )
    if text in grpc.StatusCode.__members__:
        await context.abort(
            grpc.StatusCode[text], "because", trailing_metadata=((ERROR_CODE_KEY, f"{text.lower()}_x"),)
        )
    return request


def register(server: grpc.aio.Server) -> None:
    handler = grpc.unary_unary_rpc_method_handler(
        behave, request_deserializer=bytes, response_serializer=bytes
    )
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Remote", {"Call": handler}),))


def caller(channel: grpc.aio.Channel) -> grpc.aio.UnaryUnaryMultiCallable[bytes, bytes]:
    """The test method on ``channel``, its messages sent as they are."""
    return channel.unary_unary(RPC, request_serializer=bytes, response_deserializer=bytes)


@pytest.fixture
async def call() -> AsyncGenerator[grpc.aio.UnaryUnaryMultiCallable[bytes, bytes]]:
    async with (
        serve_grpc(port=0, register=register, registry=CollectorRegistry()) as port,
        grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel,
    ):
        yield caller(channel)


async def failure(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], request: bytes, deadline: float = 2.0
) -> RemoteError:
    with pytest.raises(RemoteError) as caught:
        await call_unary(call, request, rpc=RPC, deadline=deadline)
    return caught.value


async def test_an_answer_comes_back_as_it_was_sent(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    assert await call_unary(call, b"hello", rpc=RPC) == b"hello"


async def test_a_ledger_error_of_the_other_side_comes_back_with_its_kind_and_code(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"missing")
    assert isinstance(error, RemoteNotFoundError)
    assert (error.kind, error.code, error.remote_code, error.status, error.rpc) == (
        ErrorKind.NOT_FOUND,
        "remote_not_found",
        "missing_thing",
        grpc.StatusCode.NOT_FOUND,
        RPC,
    )
    assert str(error) == "a service this one depends on answered NOT_FOUND (missing_thing)"
    assert "4111" not in str(error)
    assert RPC not in str(error)  # a client that gets the message as a problem's detail learns no RPC name


async def test_a_code_longer_than_any_stable_code_is_unknown_and_kept_cut_for_the_log(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"long")
    assert (error.remote_code, error.raw_code) == ("unknown", "x" * REMOTE_CODE_LIMIT + "...")
    assert str(error) == "a service this one depends on answered NOT_FOUND (unknown)"


async def test_a_code_that_is_not_a_stable_code_is_unknown(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"odd")
    assert (error.remote_code, error.raw_code) == ("unknown", ODD_CODE)
    assert "Bad" not in str(error)


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("missing_thing", "missing_thing"),
        ("a", "a"),
        ("a" * REMOTE_CODE_LIMIT, "a" * REMOTE_CODE_LIMIT),
        ("a" * (REMOTE_CODE_LIMIT + 1), "unknown"),
        ("", "unknown"),
        ("Missing", "unknown"),
        ("9lives", "unknown"),
        ("_private", "unknown"),
        ("kebab-case", "unknown"),
        ("two words", "unknown"),
        ("line\n", "unknown"),
        ("café", "unknown"),
    ],
)
def test_only_a_stable_code_is_taken_as_the_other_sides_code(sent: str, kept: str) -> None:
    error = RemoteNotFoundError(RPC, grpc.StatusCode.NOT_FOUND, sent)
    assert (error.remote_code, error.raw_code) == (kept, truncate(sent, REMOTE_CODE_LIMIT))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("NOT_FOUND", RemoteNotFoundError),
        ("FAILED_PRECONDITION", RemoteConflictError),
        ("ALREADY_EXISTS", RemoteConflictError),
        ("ABORTED", RemoteConflictError),
        ("PERMISSION_DENIED", RemoteForbiddenError),
        ("UNAUTHENTICATED", RemoteForbiddenError),
        ("INVALID_ARGUMENT", RemoteDefectError),
        ("OUT_OF_RANGE", RemoteDefectError),
        ("UNIMPLEMENTED", RemoteDefectError),
        ("UNAVAILABLE", RemoteUnavailableError),
        ("RESOURCE_EXHAUSTED", RemoteUnavailableError),
        ("INTERNAL", RemoteUnavailableError),
        ("UNKNOWN", RemoteUnavailableError),
        ("DATA_LOSS", RemoteUnavailableError),
    ],
)
async def test_each_status_of_the_other_side_is_a_kind_here(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], status: str, expected: type[RemoteError]
) -> None:
    error = await failure(call, status.encode())
    assert type(error) is expected
    assert error.remote_code == f"{status.lower()}_x"


async def test_an_ending_without_a_code_says_it_is_unknown(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"bare")
    assert (type(error), error.remote_code, error.raw_code) == (RemoteNotFoundError, "unknown", None)


async def test_a_call_past_its_deadline_is_unavailable(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes],
) -> None:
    error = await failure(call, b"slow", deadline=0.1)
    assert (type(error), error.status, error.kind) == (
        RemoteUnavailableError,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        ErrorKind.UNAVAILABLE,
    )


async def test_a_service_that_is_not_there_is_unavailable() -> None:
    async with grpc.aio.insecure_channel("127.0.0.1:1") as channel:
        error = await failure(caller(channel), b"hello", deadline=1.0)
    assert type(error) is RemoteUnavailableError


@pytest.mark.parametrize("deadline", [0.0, -1.0, math.inf, math.nan, MAX_DEADLINE + 1])
async def test_every_call_has_a_bounded_deadline(
    call: grpc.aio.UnaryUnaryMultiCallable[bytes, bytes], deadline: float
) -> None:
    with pytest.raises(InvalidOptionError, match="gRPC client option deadline must be"):
        await call_unary(call, b"hello", rpc=RPC, deadline=deadline)


def test_the_kinds_of_remote_errors() -> None:
    kinds: dict[type[RemoteError], ErrorKind] = {
        RemoteNotFoundError: ErrorKind.NOT_FOUND,
        RemoteConflictError: ErrorKind.CONFLICT,
        RemoteForbiddenError: ErrorKind.FORBIDDEN,
        RemoteUnavailableError: ErrorKind.UNAVAILABLE,
        RemoteDefectError: ErrorKind.INTERNAL,
    }
    assert {error: error.kind for error in kinds} == kinds
