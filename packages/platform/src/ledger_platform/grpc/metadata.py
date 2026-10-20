"""What a call carries besides its message: its caller's token, its call id and its trace.

A client sends ``call_metadata``: ``authorization: Bearer <token>`` (the caller guard's, ``grpc.guard``),
``ledger-call-id`` (the request or system call it is made for, for the logs) and the current span's
``traceparent``, so the served call continues the caller's trace (a server is reached only by its own callers,
never at the public edge). The server reads them back with the helpers here, trusting none of them blindly: a
value repeated, binary or of the wrong shape is no value.
"""

import re
from typing import Final
lazy from collections.abc import Sequence

from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
lazy from pydantic import SecretStr

from ledger_platform.observability.tracing import current_traceparent

AUTHORIZATION_KEY: Final = "authorization"
CALL_ID_KEY: Final = "ledger-call-id"
TRACEPARENT_KEY: Final = "traceparent"
_BEARER: Final = "bearer"
_MAX_TRACEPARENT_LENGTH: Final = 55
"""A version 00 ``traceparent``; a longer value is never handed to the propagator (its pattern backtracks)."""
_CALL_ID: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
"""A call id worth a log field: a request id, a system call's id. Anything else is not repeated."""
_PROPAGATOR: Final = TraceContextTextMapPropagator()

type Metadata = Sequence[tuple[str, str | bytes]]
"""A call's metadata as grpc hands it over: keys, and text values (bytes under a ``-bin`` key)."""


def call_metadata(token: SecretStr, *, call_id: str) -> tuple[tuple[str, str], ...]:
    """What a call to a guarded server carries: the caller's token, ``call_id`` and the current trace.

    The trace is the current span's ``traceparent``, left out outside one. The result holds the token: it is
    handed to grpc, never logged.
    """
    sent = ((AUTHORIZATION_KEY, f"Bearer {token.get_secret_value()}"), (CALL_ID_KEY, call_id))
    traceparent = current_traceparent()
    return sent if traceparent is None else (*sent, (TRACEPARENT_KEY, traceparent))


def metadata_value(metadata: Metadata, key: str) -> str | None:
    """The one text value ``metadata`` holds under ``key``; ``None`` when it holds none, several or bytes."""
    match [value for name, value in metadata if name == key]:
        case [str() as value]:
            return value
        case _:
            return None


def bearer_token(authorization: str | None) -> str | None:
    """The token of ``Bearer <token>`` (the scheme in any case); ``None`` for anything else or no token."""
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == _BEARER and token else None


def call_id_of(metadata: Metadata) -> str | None:
    """The call id the caller sent, when it has the shape of one."""
    call_id = metadata_value(metadata, CALL_ID_KEY)
    return call_id if call_id is not None and _CALL_ID.fullmatch(call_id) else None


def trace_context_of(metadata: Metadata) -> Context:
    """The trace the caller's ``traceparent`` names, to continue; without one, an empty one (a new trace)."""
    traceparent = metadata_value(metadata, TRACEPARENT_KEY)
    if traceparent is None or len(traceparent) > _MAX_TRACEPARENT_LENGTH:
        return Context()
    return _PROPAGATOR.extract({TRACEPARENT_KEY: traceparent}, context=Context())
