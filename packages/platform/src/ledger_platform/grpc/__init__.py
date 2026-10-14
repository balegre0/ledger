"""gRPC for cross-process surfaces (the signer)."""

from ledger_platform.grpc.client import RemoteError, call_unary
from ledger_platform.grpc.server import serve_grpc

__all__ = [
    "RemoteError",
    "call_unary",
    "serve_grpc",
]
