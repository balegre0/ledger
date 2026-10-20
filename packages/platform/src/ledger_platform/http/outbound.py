"""The clients a channel calls other parties with (§3.10 ``http``, §10 outbound, §11): one factory per app.

Every client ``OutboundHttp`` makes trusts nothing of where it runs: it follows no redirect (an answer is
returned as it came), ignores the environment's proxies and ``.netrc`` (``trust_env=False``), speaks
HTTP/1.1, and verifies TLS against the system's trust store, always: no client of a channel is ever made
without verification. What the party may do beyond that (its hosts, its addresses, the size of its answers)
is the channel's to check.

Each request is traced as a ``CLIENT`` span named ``<client> <METHOD>`` with the method, the host and the
answer's status only: never the path, the query, a header or a body (a path carries tax ids, a header a
token). A failure is named on the span by its type (``error.type``: the exception's qualified name, the
status for an answer of 400 or more, ``cancelled`` for a request cancelled in flight), never by its message.
Each request is measured in ``ledger_outbound_requests_seconds{client, outcome}``, the outcome the status
class (``2xx``) or that same failure name: labels the code bounds, never a party. Both cover the request up to
its answer's head; reading the body is the caller's.

A transport registered under a client's name replaces the network for that client (tests; the fake
authority run in process): the caller can tell (``replaced``), so a channel that must never reach a live
party but over the network refuses one.
"""

from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType
from typing import Final, Self, final, override
lazy from collections.abc import Mapping
lazy from types import TracebackType

import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
lazy from opentelemetry.trace import Tracer
lazy from prometheus_client import Histogram

from ledger_kernel.errors import error_type
from ledger_platform.observability.metrics import histogram
lazy from ledger_platform.observability.telemetry import Telemetry

OUTBOUND_SECONDS: Final = "ledger_outbound_requests_seconds"
CANCELLED: Final = "cancelled"
"""The outcome of a request cancelled in flight (a deadline, a stopping app)."""
_FAILING_STATUS: Final = 400
_CONNECTIONS: Final = 100
_KEPT: Final = 20
"""httpx's own limits: connections open at once, and kept open between requests."""
_NO_TRANSPORTS: Final[Mapping[str, httpx.AsyncBaseTransport]] = MappingProxyType({})
_METHOD: Final = "http.request.method"
_HOST: Final = "server.address"
_STATUS: Final = "http.response.status_code"
_ERROR: Final = "error.type"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class OutboundTimeouts:
    """How long a client waits, in seconds: to connect, and for each read, write or pooled connection.

    A whole call's deadline is the caller's (``asyncio.timeout``): a client bounds each step, not the sum.
    """

    connect: float
    total: float


@final
class _Traced(httpx.AsyncBaseTransport):
    """``inner`` with every request traced and measured under the client's name (see the module)."""

    __slots__ = ("_client", "_duration", "_inner", "_tracer")

    def __init__(
        self, inner: httpx.AsyncBaseTransport, *, client: str, tracer: Tracer, duration: Histogram
    ) -> None:
        self._inner = inner
        self._client = client
        self._tracer = tracer
        self._duration = duration

    @override
    async def __aenter__(self) -> Self:
        await self._inner.__aenter__()
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc_value, traceback)

    @override
    async def aclose(self) -> None:
        await self._inner.aclose()

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = perf_counter()
        outcome: str = CANCELLED  # what a request that ends neither way amounts to
        failure: str | None = CANCELLED
        with self._tracer.start_as_current_span(
            f"{self._client} {request.method}",
            kind=SpanKind.CLIENT,
            attributes={_METHOD: request.method, _HOST: request.url.host},
            record_exception=False,  # its message and traceback could quote what was sent
            set_status_on_exception=False,
        ) as span:
            try:
                response = await self._inner.handle_async_request(request)
            except Exception as error:
                outcome = failure = error_type(error)
                raise
            else:
                status = response.status_code
                span.set_attribute(_STATUS, status)
                outcome = f"{status // 100}xx"
                failure = str(status) if status >= _FAILING_STATUS else None
                return response
            finally:
                if failure is not None:
                    span.set_attribute(_ERROR, failure)
                    span.set_status(Status(StatusCode.ERROR))
                self._duration.labels(self._client, outcome).observe(perf_counter() - started)


@final
class OutboundHttp:
    """Makes the clients a channel calls out with (see the module); one per channel, from the app."""

    __slots__ = ("_duration", "_tracer", "_transports")

    def __init__(
        self, *, telemetry: Telemetry, transports: Mapping[str, httpx.AsyncBaseTransport] | None = None
    ) -> None:
        self._tracer = telemetry.tracer
        self._duration = histogram(
            telemetry.registry,
            OUTBOUND_SECONDS,
            "Seconds an outbound request took to its answer's head, by client and outcome.",
            ("client", "outcome"),
        )
        self._transports = _NO_TRANSPORTS if transports is None else MappingProxyType(dict(transports))

    def replaced(self, name: str) -> bool:
        """Whether a registered transport replaces the network for the client ``name``."""
        return name in self._transports

    def client(self, name: str, *, timeouts: OutboundTimeouts, keep_alive: bool = True) -> httpx.AsyncClient:
        """A new client named ``name`` (its spans and metrics), to be used as an ``async with`` block.

        ``keep_alive`` ``False``: no connection is kept for another request (parties whose addresses are
        checked before each request, so a connection is never reused for an address not checked again).
        """
        inner = self._transports.get(name)
        if inner is None:
            limits = httpx.Limits(
                max_connections=_CONNECTIONS, max_keepalive_connections=_KEPT if keep_alive else 0
            )
            inner = httpx.AsyncHTTPTransport(verify=True, trust_env=False, http2=False, limits=limits)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeouts.total, connect=timeouts.connect),
            follow_redirects=False,
            trust_env=False,
            transport=_Traced(inner, client=name, tracer=self._tracer, duration=self._duration),
        )
