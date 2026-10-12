"""Calls to other services' gRPC methods: always with a deadline, their errors back as ledger errors.

A chassis server ends a failed call with the status of the error's kind and the error's code in the trailing
metadata ``ledger-error-code`` (``grpc_server``). ``call_unary`` turns that back into a ``RemoteError`` of the
matching kind, keeping the remote code, so that a route that called another service answers as if the error
were its own: a company the other service does not have is a 404 here too. What the other side wrote in its
details is not repeated (it may quote what was asked), its code is cut at ``REMOTE_CODE_LIMIT``, and the
message names no call: a client that gets it as a problem's ``detail`` learns nothing of the services behind
the API, while ``RemoteError.rpc`` names the call for the log.

A call's deadline is fixed by its caller, at most ``MAX_DEADLINE``. Deriving it from the remaining deadline of
the request being served, and retrying idempotent methods, come with the first service-to-service caller
(milestone 3), as the spec records (§4.2).

Statuses map so: ``NOT_FOUND`` is not found; ``FAILED_PRECONDITION``, ``ALREADY_EXISTS`` and ``ABORTED`` are a
conflict; ``PERMISSION_DENIED`` and ``UNAUTHENTICATED`` are forbidden (this service, not its client, was
refused); ``INVALID_ARGUMENT``, ``OUT_OF_RANGE`` and ``UNIMPLEMENTED`` are a defect of ours (we sent what the
other side cannot take); anything else, a passed deadline and the other side's own defects included, is
unavailable: a dependency failed, and a retry may work.
"""

from typing import ClassVar, Final
lazy from collections.abc import Mapping

import grpc

from ledger_kernel.errors import ErrorKind, LedgerError, truncate
from ledger_platform.options import InvalidOptionError, require_seconds

ERROR_CODE_KEY: Final = "ledger-error-code"
"""The trailing metadata that carries a failed call's stable code."""
DEFAULT_DEADLINE: Final = 2.0
MAX_DEADLINE: Final = 30.0
"""No call between services waits longer: a request's own deadline is shorter."""
REMOTE_CODE_LIMIT: Final = 64
"""What is kept of the other side's code: a stable code is a short word, anything longer is cut."""
_UNKNOWN_CODE: Final = "unknown"


class InvalidGrpcClientOptionError(InvalidOptionError):
    """A gRPC client option that cannot work."""

    code: ClassVar[str] = "invalid_grpc_client_option"
    label: ClassVar[str] = "gRPC client option"


class RemoteError(LedgerError):
    """Another service answered a call with an error: which call, its status and the other side's code.

    The message says the status and the code only; ``rpc`` is for the log.
    """

    code: ClassVar[str] = "remote_error"

    rpc: str
    status: grpc.StatusCode
    remote_code: str

    def __init__(self, rpc: str, status: grpc.StatusCode, remote_code: str) -> None:
        kept = truncate(remote_code, REMOTE_CODE_LIMIT)
        super().__init__(f"a service this one depends on answered {status.name} ({kept})")
        self.rpc = rpc
        self.status = status
        self.remote_code = kept


class RemoteNotFoundError(RemoteError):
    """The other service does not have what was asked for."""

    kind: ClassVar[ErrorKind] = ErrorKind.NOT_FOUND
    code: ClassVar[str] = "remote_not_found"


class RemoteConflictError(RemoteError):
    """The other service's state forbids the call."""

    kind: ClassVar[ErrorKind] = ErrorKind.CONFLICT
    code: ClassVar[str] = "remote_conflict"


class RemoteForbiddenError(RemoteError):
    """The other service refused this service."""

    kind: ClassVar[ErrorKind] = ErrorKind.FORBIDDEN
    code: ClassVar[str] = "remote_forbidden"


class RemoteUnavailableError(RemoteError):
    """The other service could not answer: down, too slow, or failing; a retry may work."""

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "remote_unavailable"


class RemoteDefectError(RemoteError):
    """The other service could not take what this one sent: a defect of ours."""

    code: ClassVar[str] = "remote_defect"


_BY_STATUS: Final[Mapping[grpc.StatusCode, type[RemoteError]]] = {
    grpc.StatusCode.NOT_FOUND: RemoteNotFoundError,
    grpc.StatusCode.FAILED_PRECONDITION: RemoteConflictError,
    grpc.StatusCode.ALREADY_EXISTS: RemoteConflictError,
    grpc.StatusCode.ABORTED: RemoteConflictError,
    grpc.StatusCode.PERMISSION_DENIED: RemoteForbiddenError,
    grpc.StatusCode.UNAUTHENTICATED: RemoteForbiddenError,
    grpc.StatusCode.INVALID_ARGUMENT: RemoteDefectError,
    grpc.StatusCode.OUT_OF_RANGE: RemoteDefectError,
    grpc.StatusCode.UNIMPLEMENTED: RemoteDefectError,
}


def require_deadline(deadline: float) -> None:
    """Refuse a call deadline that is not a positive, finite number of seconds up to ``MAX_DEADLINE``.

    For a client that takes its deadline once, to refuse it when it is made rather than on its first call.
    """
    require_seconds(InvalidGrpcClientOptionError, "deadline", deadline, ceiling=MAX_DEADLINE)


def remote_error(error: grpc.aio.AioRpcError, rpc: str) -> RemoteError:
    """The ledger error a failed call to ``rpc`` stands for (see the module)."""
    code = dict(error.trailing_metadata() or ()).get(ERROR_CODE_KEY)
    status = error.code()
    return _BY_STATUS.get(status, RemoteUnavailableError)(
        rpc, status, code if isinstance(code, str) else _UNKNOWN_CODE
    )


async def call_unary[Request, Response](
    method: grpc.aio.UnaryUnaryMultiCallable[Request, Response],
    request: Request,
    *,
    rpc: str,
    deadline: float = DEFAULT_DEADLINE,
) -> Response:
    """Call ``method`` (named ``rpc``, ``/package.Service/Method``) within ``deadline`` seconds.

    A failure is the ``RemoteError`` the module describes; the deadline must be positive and at most
    ``MAX_DEADLINE``.
    """
    require_deadline(deadline)
    try:
        return await method(request, timeout=deadline)
    except grpc.aio.AioRpcError as error:
        raise remote_error(error, rpc) from error
