"""gRPC for cross-process surfaces (the signer): guarded servers, clients with deadlines and their callers."""

from ledger_platform.grpc._handlers import MalformedRequestError
from ledger_platform.grpc.client import INTERNAL_CODE, RemoteError, call_stream_unary, call_unary
from ledger_platform.grpc.guard import (
    CallerPolicy,
    CallerTokenRefusedError,
    CallerTokens,
    CallerUnboundError,
    CallerUnknownError,
    MethodNotAllowedError,
    calling_as,
    current_caller,
)
from ledger_platform.grpc.metadata import call_metadata
from ledger_platform.grpc.server import serve_grpc

__all__ = [
    "INTERNAL_CODE",
    "CallerPolicy",
    "CallerTokenRefusedError",
    "CallerTokens",
    "CallerUnboundError",
    "CallerUnknownError",
    "MalformedRequestError",
    "MethodNotAllowedError",
    "RemoteError",
    "call_metadata",
    "call_stream_unary",
    "call_unary",
    "calling_as",
    "current_caller",
    "serve_grpc",
]
