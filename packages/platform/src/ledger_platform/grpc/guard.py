"""Who may call a gRPC server, and what: per-caller tokens and method allowlists (ADR 0010, spec §3.7).

Until a service mesh adds mTLS (spec §16.4), a server knows its callers by a token each holds
(``authorization: Bearer <token>``, ``grpc.metadata.call_metadata``), and lets each call only the methods of
its allowlist. ``CallerTokens`` holds both, built once at start: it refuses a caller without a token, a token
shorter than ``MIN_TOKEN_LENGTH`` or of other than visible ASCII characters, and one token held by two callers
(a stolen token would then speak for both). During a rotation a caller holds its current token and its
previous one. The tokens are kept as SHA-256 digests only, and a presented token is compared with every one of
them by ``hmac.compare_digest``: neither what is stored nor how long a comparison takes tells a token.

``CallerGuard`` checks every call of a registered method before the method runs and before its request is
read (``_handlers``: the request is parsed only once the call is admitted): a call that names no known caller
ends ``UNAUTHENTICATED`` (``grpc_caller_unknown``), one outside its caller's allowlist ``PERMISSION_DENIED``
(``grpc_method_not_allowed``); both are logged as ``grpc.caller_refused`` (the method, the code, the peer's
address, the caller when known, never what was presented; not rate limited) and traced in a trace of their
own. A synchronous method is never served: grpc would run it on a thread, where the caller cannot be bound; a
call to one fails as a defect of the service. An admitted method runs with its caller bound
(``current_caller``) and named in the logs with the method and the call id it was sent, inside a server span
named after the method that continues the caller's trace (attributes: the method and the caller only).
"""

import hashlib
import hmac
import re
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import ClassVar, Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Sequence

import grpc
import structlog
from opentelemetry.context import Context as TraceContext
from opentelemetry.trace import SpanKind, Status, StatusCode
lazy from opentelemetry.trace import Tracer
lazy from pydantic import SecretStr

from ledger_kernel.errors import ForbiddenError, LedgerError, UnauthenticatedError, error_type
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.grpc._handlers import rewrapped
from ledger_platform.grpc.metadata import (
    AUTHORIZATION_KEY,
    bearer_token,
    call_id_of,
    metadata_value,
    trace_context_of,
)
lazy from ledger_platform.grpc._handlers import Context, CoroutineMethod, GeneratorMethod
lazy from ledger_platform.grpc.metadata import Metadata

MIN_TOKEN_LENGTH: Final = 32
"""The fewest characters of a caller's token."""
_VISIBLE_ASCII: Final = re.compile(r"[\x21-\x7e]*", re.ASCII)
"""What a token is made of: what an ``authorization`` value carries as it is, no space, no control."""
_CALLER_NAME: Final = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)
_COMPONENT: Final = "gRPC guard"
"""What this module's option errors name (``InvalidOptionError.component``)."""
_EXCEPTION: Final = "exception"
_EXCEPTION_TYPE: Final = "exception.type"
_logger = structlog.stdlib.get_logger(__name__)


class CallerUnknownError(UnauthenticatedError):
    """A call named no known caller: no token, a malformed one, or one no caller holds."""

    code: ClassVar[str] = "grpc_caller_unknown"

    def __init__(self) -> None:
        super().__init__("the caller is not known")


class MethodNotAllowedError(ForbiddenError):
    """A known caller called a method outside its allowlist."""

    code: ClassVar[str] = "grpc_method_not_allowed"

    def __init__(self) -> None:
        super().__init__("the caller may not call this method")


class CallerTokenRefusedError(LedgerError):
    """A caller's tokens cannot guard a server: a defect of the deployment, found at start.

    The message names the caller and what is wrong, never a token.
    """

    code: ClassVar[str] = "grpc_caller_token_refused"

    def __init__(self, caller: str, reason: str) -> None:
        super().__init__(f"gRPC caller {caller} {reason}")


class CallerUnboundError(LedgerError):
    """``current_caller`` was asked outside a guarded call: a defect of the code path."""

    code: ClassVar[str] = "grpc_caller_unbound"

    def __init__(self) -> None:
        super().__init__("no gRPC caller is bound: current_caller is for the methods of a guarded server")


class SynchronousMethodError(LedgerError):
    """A guarded server was given a synchronous method, which grpc would run outside the guard."""

    code: ClassVar[str] = "grpc_method_synchronous"

    def __init__(self, rpc: str) -> None:
        super().__init__(
            f"the gRPC method {rpc} is synchronous: a guarded server serves asynchronous ones only"
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CallerPolicy:
    """A caller of a server: its name, the tokens it calls with, and the methods it may call."""

    name: str
    """``snake_case``: ``api``, ``worker``, ``receiver``. What the audit and the logs name the caller by."""
    tokens: tuple[SecretStr, ...] = field(repr=False)
    """Its current token and, during a rotation, its previous one."""
    methods: frozenset[str]
    """Full names: ``/ledger.signer.v1.SignerService/SignXml``."""


def _digest(token: str) -> bytes:
    """What a token is kept and compared as.

    Never raises: what cannot be UTF-8 is escaped (and matches no token: a token is visible ASCII).
    """
    return hashlib.sha256(token.encode("utf-8", "backslashreplace")).digest()


def _digests_of(policy: CallerPolicy) -> frozenset[bytes]:
    """The digests of ``policy``'s tokens, once each; ``CallerTokenRefusedError`` for one unfit to guard."""
    if not policy.tokens:
        raise CallerTokenRefusedError(policy.name, "has no token")
    for token in policy.tokens:
        value = token.get_secret_value()
        if len(value) < MIN_TOKEN_LENGTH:
            raise CallerTokenRefusedError(
                policy.name, f"has a token shorter than {MIN_TOKEN_LENGTH} characters"
            )
        if not _VISIBLE_ASCII.fullmatch(value):
            raise CallerTokenRefusedError(policy.name, "has a token of other than visible ASCII characters")
    return frozenset(_digest(token.get_secret_value()) for token in policy.tokens)


@final
class CallerTokens:
    """Who may call what: built once, refuses a missing (empty), short (< 32 chars) or shared token.

    Also refuses no caller at all, and a caller named twice or not in ``snake_case`` (``InvalidOptionError``).
    """

    __slots__ = ("_digests", "_methods")

    def __init__(self, policies: Sequence[CallerPolicy]) -> None:
        if not policies:
            raise InvalidOptionError(
                component=_COMPONENT, option="callers", requirement="at least one caller", value=len(policies)
            )
        digests: list[tuple[bytes, str]] = []
        methods: dict[str, frozenset[str]] = {}
        for policy in policies:
            if not _CALLER_NAME.fullmatch(policy.name):
                raise InvalidOptionError(
                    component=_COMPONENT, option="name", requirement="snake_case", value=policy.name
                )
            if policy.name in methods:
                raise InvalidOptionError(
                    component=_COMPONENT, option="name", requirement="one per caller", value=policy.name
                )
            own = _digests_of(policy)
            if any(digest in own for digest, _ in digests):
                raise CallerTokenRefusedError(policy.name, "has a token another caller has too")
            methods[policy.name] = policy.methods
            digests.extend((digest, policy.name) for digest in own)
        self._digests: Final = tuple(digests)
        self._methods: Final = methods

    def caller_of(self, authorization: str | None) -> str | None:
        """The caller whose token ``authorization`` (``Bearer <token>``) presents; ``None`` for nobody.

        Constant-time over SHA-256 digests: the presented token's digest is compared with every stored one,
        whichever matches.
        """
        token = bearer_token(authorization)
        if token is None:
            return None
        presented = _digest(token)
        found: str | None = None
        for digest, name in self._digests:
            if hmac.compare_digest(digest, presented):
                found = name
        return found

    def allows(self, caller: str, method: str) -> bool:
        """Whether ``caller`` may call ``method`` (its full name); a caller nobody named may call nothing."""
        return method in self._methods.get(caller, frozenset())

    @override
    def __repr__(self) -> str:
        return f"CallerTokens(callers={tuple(self._methods)!r})"


_CALLER: Final[ContextVar[str | None]] = ContextVar("ledger_grpc_caller", default=None)


@contextmanager
def calling_as(caller: str) -> Generator[None]:
    """``caller`` is the caller for the length of the block (the outer one is restored after it).

    The guard binds it around every admitted call; a test binds it to call a servicer's method directly.
    """
    token = _CALLER.set(caller)
    try:
        yield
    finally:
        _CALLER.reset(token)


def current_caller() -> str:
    """The guarded call's caller (``CallerUnboundError`` outside one)."""
    caller = _CALLER.get()
    if caller is None:
        raise CallerUnboundError
    return caller


@final
class _Admission:
    """One call of ``rpc``: refused before its method runs, or run with its caller bound, in a server span."""

    __slots__ = ("_allowed", "_call_id", "_caller", "_parent", "_rpc", "_tracer")

    def __init__(
        self, *, rpc: str, caller: str | None, allowed: bool, metadata: Metadata, tracer: Tracer
    ) -> None:
        self._rpc = rpc
        self._caller = caller
        self._allowed = allowed
        self._call_id = call_id_of(metadata)
        self._parent = trace_context_of(metadata)
        self._tracer = tracer

    def _refusal(self, context: Context) -> LedgerError:
        """Why this call is refused, logged with who sent it (the peer's address), never what it presented.

        Every refusal is a line, not rate limited: the port is reachable from the callers' pods only.
        """
        refusal = CallerUnknownError() if self._caller is None else MethodNotAllowedError()
        known = {} if self._caller is None else {"caller": self._caller}
        _logger.warning("grpc.caller_refused", rpc=self._rpc, code=refusal.code, peer=context.peer(), **known)
        return refusal

    @contextmanager
    def _traced(self, parent: TraceContext) -> Generator[None]:
        """A server span of the call under ``parent``.

        An ``Exception`` marks the span failed with the error's type only (``tracing.Tracing.consuming``'s
        rule: spans leave the process, messages can quote data) and propagates unchanged.
        """
        attributes = {"ledger.rpc": self._rpc}
        if self._caller is not None:
            attributes["ledger.caller"] = self._caller
        with self._tracer.start_as_current_span(
            self._rpc,
            context=parent,
            kind=SpanKind.SERVER,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event(_EXCEPTION, {_EXCEPTION_TYPE: error_type(error)})
                raise

    @contextmanager
    def _admitted(self, context: Context) -> Generator[None]:
        """The call admitted, in its span, its caller bound and named in the logs; else its refusal, raised.

        Only an admitted call continues its caller's trace: a refused one starts a trace of its own, so that
        a caller nobody knows cannot attach spans to a trace it names.
        """
        caller = self._caller if self._allowed else None
        if caller is None:
            refusal = self._refusal(context)
            with self._traced(TraceContext()):
                raise refusal
        else:
            fields = {"rpc": self._rpc, "caller": caller}
            if self._call_id is not None:
                fields["call_id"] = self._call_id
            with (
                self._traced(self._parent),
                calling_as(caller),
                structlog.contextvars.bound_contextvars(**fields),
            ):
                yield

    def coroutine[Raw, Input](
        self, behavior: CoroutineMethod[Input], parse: Callable[[Raw], Input]
    ) -> CoroutineMethod[Raw]:
        async def admitted(request: Raw, context: Context) -> object:
            with self._admitted(context):
                return await behavior(parse(request), context)

        return admitted

    def generator[Raw, Input](
        self, behavior: GeneratorMethod[Input], parse: Callable[[Raw], Input]
    ) -> GeneratorMethod[Raw]:
        async def admitted(request: Raw, context: Context) -> AsyncGenerator[object]:
            with self._admitted(context):
                async with aclosing(behavior(parse(request), context)) as responses:
                    async for response in responses:
                        yield response

        return admitted

    def synchronous(self) -> CoroutineMethod[object]:
        async def refused(request: object, context: Context) -> object:
            del request
            with self._admitted(context):
                raise SynchronousMethodError(self._rpc)

        return refused


@final
class CallerGuard(grpc.aio.ServerInterceptor):
    """Admits every call of a registered method by its caller's token and allowlist (see the module).

    It sits inside ``server.GuardInterceptor``, which answers its refusals and times them like any call; a
    method nobody registered is left to grpc (``UNIMPLEMENTED``, untimed).
    """

    __slots__ = ("_callers", "_tracer")

    def __init__(self, callers: CallerTokens, tracer: Tracer) -> None:
        self._callers = callers
        self._tracer = tracer

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
            return None
        rpc, metadata = handler_call_details.method, handler_call_details.invocation_metadata
        caller = self._callers.caller_of(metadata_value(metadata, AUTHORIZATION_KEY))
        admission = _Admission(
            rpc=rpc,
            caller=caller,
            allowed=caller is not None and self._callers.allows(caller, rpc),
            metadata=metadata,
            tracer=self._tracer,
        )
        return rewrapped(handler, admission)
