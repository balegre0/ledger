"""The clients a channel calls other parties with (§3.10 ``http``, §10 outbound, §11): a factory per channel.

Every client ``OutboundHttp`` makes trusts nothing of where it runs. It follows no redirect (an answer is
returned as it came). It speaks HTTP/1.1 only. It verifies TLS, always, against the authorities httpx bundles
(``certifi``): no client of a channel is made without verification. And it reads nothing of the environment
(``trust_env=False``, on the client and on its transport): no proxy variable reroutes it, and no
``SSL_CERT_FILE``/``SSL_CERT_DIR`` adds an authority it would trust. What the party may do beyond that (its
hosts, its addresses, the size of its answers) is the channel's to check.

**Time.** ``OutboundTimeouts``: ``connect`` bounds connecting, ``each`` every single read, write or wait for
a pooled connection, and ``deadline`` the whole request up to its answer's head
(``OutboundDeadlineError``, an ``httpx.TimeoutException``): a party that drips its answer byte by byte never
holds a client past it. Reading the body after the head is the caller's to bound (a size and a time of its
own), as is a call made of several requests.

**Telemetry.** Each request is traced as a ``CLIENT`` span named ``<client> <METHOD>`` with the method, the
host and the answer's status only: never the path, the query, a header or a body (a path carries tax ids, a
header a token). The host is the name the request was meant for: with a pinned address (``sni_hostname``),
its ``Host``. A failure is named on the span by its type (``error.type``: the exception's qualified name, the
status for an answer of 400 or more, ``cancelled`` for a request cancelled in flight), never by its message.
Each request is measured in ``ledger_outbound_requests_seconds{client, outcome}`` (buckets to a minute), the
outcome the status class (``2xx``) or that same failure name: labels the code bounds, never a party. Both
cover the request up to its answer's head; a body that then breaks is counted in
``ledger_outbound_body_failures_total{client, outcome}``. httpx's own log lines (full URLs at INFO) are
silenced by the platform's logging (``observability.logs``).

**Registered transports.** A transport registered under a client's name replaces the network for that client
(tests): the caller can tell (``replaced``), so a channel that must never reach a live party but over the
network refuses one. A registered transport is the registrant's: no client opens or closes it, so one
transport serves several names and several clients in turn.
"""

import asyncio
from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType
from typing import Final, Self, final, override
lazy from collections.abc import AsyncIterator, Mapping
lazy from types import TracebackType

import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
lazy from opentelemetry.trace import Tracer
lazy from prometheus_client import Counter, Histogram

from ledger_kernel.errors import error_type
from ledger_platform.observability.metrics import counter, histogram
lazy from ledger_platform.observability.telemetry import Telemetry

OUTBOUND_SECONDS: Final = "ledger_outbound_requests_seconds"
OUTBOUND_BODY_FAILURES: Final = "ledger_outbound_body_failures"
OUTBOUND_BUCKETS: Final = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0)
"""Seconds: an authority's call may take up to its minute."""
CANCELLED: Final = "cancelled"
"""The outcome of a request cancelled in flight (a stopping app, a caller's own deadline)."""
_FAILING_STATUS: Final = 400
_CONNECTIONS: Final = 100
_KEPT: Final = 20
"""httpx's own limits: connections open at once, and kept open between requests."""
_PINNED: Final = "sni_hostname"
"""The request extension that names the TLS host of a request sent to an address."""
_NO_TRANSPORTS: Final[Mapping[str, httpx.AsyncBaseTransport]] = MappingProxyType({})
_METHOD: Final = "http.request.method"
_HOST: Final = "server.address"
_STATUS: Final = "http.response.status_code"
_ERROR: Final = "error.type"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class OutboundTimeouts:
    """How long a client waits, in seconds (see the module): to connect, at each step, in all to a head."""

    connect: float
    each: float
    """Every single read, write or wait for a pooled connection."""
    deadline: float
    """The whole request, from sending to its answer's head."""


class OutboundDeadlineError(httpx.TimeoutException):
    """No answer's head within the client's ``deadline``: the request may have reached the party."""

    def __init__(self, deadline: float, request: httpx.Request) -> None:
        super().__init__(f"no answer within the deadline of {deadline} s", request=request)


def _host(request: httpx.Request) -> str:
    """The host the request was meant for: its ``Host`` when it is sent to a pinned address."""
    if _PINNED not in request.extensions:
        return request.url.host
    return httpx.URL(f"https://{request.headers['host']}").host


@final
class _Watched(httpx.AsyncByteStream):
    """A body whose failure is counted under its client before it is raised."""

    __slots__ = ("_client", "_failures", "_inner")

    def __init__(self, inner: httpx.AsyncByteStream, *, client: str, failures: Counter) -> None:
        self._inner = inner
        self._client = client
        self._failures = failures

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._inner:
                yield chunk
        except Exception as error:
            self._failures.labels(self._client, error_type(error)).inc()
            raise

    @override
    async def aclose(self) -> None:
        await self._inner.aclose()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Instruments:
    tracer: Tracer
    duration: Histogram
    failures: Counter


@final
class _Traced(httpx.AsyncBaseTransport):
    """``inner`` with every request held to the deadline, traced and measured (see the module).

    ``owned``: the network's transport, made for this client, opened and closed with it; a registered one is
    left to its registrant.
    """

    __slots__ = ("_client", "_deadline", "_inner", "_instruments", "_owned")

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        client: str,
        deadline: float,
        owned: bool,
        instruments: _Instruments,
    ) -> None:
        self._inner = inner
        self._client = client
        self._deadline = deadline
        self._owned = owned
        self._instruments = instruments

    @override
    async def __aenter__(self) -> Self:
        if self._owned:
            await self._inner.__aenter__()
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        if self._owned:
            await self._inner.__aexit__(exc_type, exc_value, traceback)

    @override
    async def aclose(self) -> None:
        if self._owned:
            await self._inner.aclose()

    async def _head(self, request: httpx.Request) -> httpx.Response:
        """``inner``'s answer, its head in by the deadline (``OutboundDeadlineError`` otherwise)."""
        scope = asyncio.timeout(self._deadline)
        try:
            async with scope:
                return await self._inner.handle_async_request(request)
        except TimeoutError as error:
            if not scope.expired():  # not ours: a transport's own
                raise
            raise OutboundDeadlineError(self._deadline, request) from error

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        instruments = self._instruments
        started = perf_counter()
        outcome: str = CANCELLED  # what a request that ends neither way amounts to
        failure: str | None = CANCELLED
        with instruments.tracer.start_as_current_span(
            f"{self._client} {request.method}",
            kind=SpanKind.CLIENT,
            attributes={_METHOD: request.method, _HOST: _host(request)},
            record_exception=False,  # its message and traceback could quote what was sent
            set_status_on_exception=False,
        ) as span:
            try:
                response = await self._head(request)
            except Exception as error:
                outcome = failure = error_type(error)
                raise
            else:
                status = response.status_code
                span.set_attribute(_STATUS, status)
                outcome = f"{status // 100}xx"
                failure = str(status) if status >= _FAILING_STATUS else None
                body = response.stream
                if isinstance(body, httpx.AsyncByteStream):  # else httpx refuses it in an async client
                    response.stream = _Watched(body, client=self._client, failures=instruments.failures)
                return response
            finally:
                if failure is not None:
                    span.set_attribute(_ERROR, failure)
                    span.set_status(Status(StatusCode.ERROR))
                instruments.duration.labels(self._client, outcome).observe(perf_counter() - started)


@final
class OutboundHttp:
    """Makes the clients a channel calls out with (see the module); one per channel, from the app."""

    __slots__ = ("_instruments", "_transports")

    def __init__(
        self, *, telemetry: Telemetry, transports: Mapping[str, httpx.AsyncBaseTransport] | None = None
    ) -> None:
        registry = telemetry.registry
        self._instruments = _Instruments(
            tracer=telemetry.tracer,
            duration=histogram(
                registry,
                OUTBOUND_SECONDS,
                "Seconds an outbound request took to its answer's head, by client and outcome.",
                ("client", "outcome"),
                buckets=OUTBOUND_BUCKETS,
            ),
            failures=counter(
                registry,
                OUTBOUND_BODY_FAILURES,
                "Outbound answers whose body broke after their head came, by client and error type.",
                ("client", "outcome"),
            ),
        )
        self._transports = _NO_TRANSPORTS if transports is None else MappingProxyType(dict(transports))

    def replaced(self, name: str) -> bool:
        """Whether a registered transport replaces the network for the client ``name``."""
        return name in self._transports

    def client(self, name: str, *, timeouts: OutboundTimeouts, keep_alive: bool = True) -> httpx.AsyncClient:
        """A new client named ``name`` (its spans and metrics), to be used as an ``async with`` block.

        ``keep_alive`` ``False``: no connection is kept for another request. For parties reached at an
        address checked before each request (the buyers', pinned with ``sni_hostname``): httpx's pool keys a
        connection by the address it dialled, not by the TLS name it verified, so a kept connection could
        serve a later request for another name, and an address no longer checked.
        """
        registered = self._transports.get(name)
        transport: httpx.AsyncBaseTransport
        if registered is None:
            limits = httpx.Limits(
                max_connections=_CONNECTIONS, max_keepalive_connections=_KEPT if keep_alive else 0
            )
            transport = httpx.AsyncHTTPTransport(verify=True, trust_env=False, http2=False, limits=limits)
        else:
            transport = registered
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeouts.each, connect=timeouts.connect),
            follow_redirects=False,
            trust_env=False,
            transport=_Traced(
                transport,
                client=name,
                deadline=timeouts.deadline,
                owned=registered is None,
                instruments=self._instruments,
            ),
        )
