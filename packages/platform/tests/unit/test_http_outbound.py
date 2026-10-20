"""``OutboundHttp`` (§3.10, §10, §11): the clients a channel calls out with, untrusting and traced."""

import asyncio
from typing import Final, Self, final, override
lazy from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from prometheus_client import CollectorRegistry

from ledger_platform.http.outbound import OUTBOUND_SECONDS, OutboundHttp, OutboundTimeouts
from ledger_platform.observability.telemetry import Telemetry

QUICK: Final = OutboundTimeouts(connect=2.0, total=5.0)
OK: Final = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nok"
MOVED: Final = b"HTTP/1.1 302 Found\r\nLocation: https://elsewhere.invalid/\r\nContent-Length: 0\r\n\r\n"
HEAD_END: Final = b"\r\n\r\n"


@final
class Origin:
    """A plain HTTP/1.1 origin on 127.0.0.1 answering every request with ``answer``; it counts connections."""

    def __init__(self, answer: bytes) -> None:
        self.answer = answer
        self.connections = 0
        self.requests = 0
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/taxpayers/00112345678?rnc=00112345678"

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                await reader.readuntil(HEAD_END)
                self.requests += 1
                writer.write(self.answer)
                await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()


async def serving(answer: bytes) -> AsyncIterator[Origin]:
    origin = Origin(answer)
    server = await asyncio.start_server(origin.serve, "127.0.0.1", 0)
    origin.port = server.sockets[0].getsockname()[1]
    try:
        yield origin
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()


@pytest.fixture
async def origin() -> AsyncIterator[Origin]:
    async for served in serving(OK):
        yield served


@pytest.fixture
async def moved() -> AsyncIterator[Origin]:
    async for served in serving(MOVED):
        yield served


@final
class Traced:
    """Telemetry whose spans a test reads, with a registry of its own."""

    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.registry = CollectorRegistry()
        self.telemetry = Telemetry(
            logger=structlog.stdlib.get_logger("probe"),
            tracer=provider.get_tracer("probe"),
            registry=self.registry,
        )

    def observed(self, client: str, outcome: str) -> float | None:
        return self.registry.get_sample_value(
            f"{OUTBOUND_SECONDS}_count", {"client": client, "outcome": outcome}
        )


@pytest.fixture
def traced() -> Traced:
    return Traced()


async def test_a_redirect_is_answered_as_it_came(moved: Origin, traced: Traced) -> None:
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        response = await client.get(moved.url)
    assert response.status_code == httpx.codes.FOUND
    assert moved.requests == 1


async def test_the_environment_s_proxies_are_ignored(
    origin: Origin, traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        response = await client.get(origin.url)
    assert (response.status_code, response.content) == (httpx.codes.OK, b"ok")
    assert origin.requests == 1


@pytest.mark.parametrize(("keep_alive", "connections"), [(True, 1), (False, 2)])
async def test_without_keep_alive_each_request_has_a_connection_of_its_own(
    origin: Origin, traced: Traced, *, keep_alive: bool, connections: int
) -> None:
    outbound = OutboundHttp(telemetry=traced.telemetry)
    async with outbound.client("probe", timeouts=QUICK, keep_alive=keep_alive) as client:
        for _ in range(2):
            assert (await client.get(origin.url)).status_code == httpx.codes.OK
    assert (origin.requests, origin.connections) == (2, connections)


async def test_a_registered_transport_replaces_the_network_for_its_name_only(traced: Traced) -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(httpx.codes.ACCEPTED)

    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"dgii": httpx.MockTransport(answer)})
    assert outbound.replaced("dgii")
    assert not outbound.replaced("buyers")
    async with outbound.client("dgii", timeouts=QUICK) as client:
        response = await client.get("https://ecf.dgii.gov.do/testecf/x")
    assert (response.status_code, seen) == (httpx.codes.ACCEPTED, ["ecf.dgii.gov.do"])


async def test_the_timeouts_are_the_client_s(traced: Traced) -> None:
    timeouts = OutboundTimeouts(connect=1.5, total=7.0)
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=timeouts) as client:
        assert client.timeout == httpx.Timeout(7.0, connect=1.5)
        assert not client.follow_redirects
        assert not client.trust_env


async def test_a_request_is_a_client_span_naming_its_host_and_status_never_its_path(
    origin: Origin, traced: Traced
) -> None:
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        await client.get(origin.url)
    [span] = traced.spans.get_finished_spans()
    assert span.name == "probe GET"
    assert span.kind is SpanKind.CLIENT
    assert dict(span.attributes or {}) == {
        "http.request.method": "GET",
        "server.address": "127.0.0.1",
        "http.response.status_code": 200,
    }
    assert span.status.status_code is StatusCode.UNSET
    assert traced.observed("probe", "2xx") == 1


async def test_a_failing_status_marks_the_span_and_the_outcome(traced: Traced) -> None:
    outbound = OutboundHttp(
        telemetry=traced.telemetry,
        transports={"probe": httpx.MockTransport(lambda _: httpx.Response(httpx.codes.BAD_GATEWAY))},
    )
    async with outbound.client("probe", timeouts=QUICK) as client:
        await client.post("https://party.invalid/fe/recepcion/api/ecf", content=b"<ECF/>")
    [span] = traced.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert (span.attributes or {})["error.type"] == "502"
    assert traced.observed("probe", "5xx") == 1


async def test_a_transport_error_is_named_by_its_type_and_raised(traced: Traced) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        quoting = "connection refused to 00112345678"
        raise httpx.ConnectError(quoting, request=request)

    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"probe": httpx.MockTransport(refuse)})
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://party.invalid/")
    [span] = traced.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert dict(span.attributes or {}) == {
        "http.request.method": "GET",
        "server.address": "party.invalid",
        "error.type": "httpx.ConnectError",
    }
    assert not span.events  # no exception event: its message could quote what was sent
    assert traced.observed("probe", "httpx.ConnectError") == 1


async def test_a_request_cancelled_in_flight_is_counted_as_cancelled(traced: Traced) -> None:
    started = asyncio.Event()

    async def stall(request: httpx.Request) -> httpx.Response:
        del request
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(httpx.codes.OK)  # never reached

    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"probe": httpx.MockTransport(stall)})
    async with outbound.client("probe", timeouts=QUICK) as client:
        request = asyncio.create_task(client.get("https://party.invalid/"))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    [span] = traced.spans.get_finished_spans()
    assert (span.attributes or {})["error.type"] == "cancelled"
    assert traced.observed("probe", "cancelled") == 1


async def test_clients_of_one_registry_share_its_histogram(origin: Origin, traced: Traced) -> None:
    for name in ("dgii", "buyers"):
        async with OutboundHttp(telemetry=traced.telemetry).client(name, timeouts=QUICK) as client:
            await client.get(origin.url)
    assert (traced.observed("dgii", "2xx"), traced.observed("buyers", "2xx")) == (1, 1)


@final
class Closing(httpx.MockTransport):
    """Answers 204 and counts how often it was entered and closed."""

    def __init__(self) -> None:
        super().__init__(lambda _: httpx.Response(httpx.codes.NO_CONTENT))
        self.entered = 0
        self.closed = 0

    @override
    async def __aenter__(self) -> Self:
        self.entered += 1
        return self

    @override
    async def aclose(self) -> None:
        self.closed += 1


async def test_a_registered_transport_is_opened_and_closed_with_its_client(traced: Traced) -> None:
    closing = Closing()
    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"probe": closing})
    async with outbound.client("probe", timeouts=QUICK) as client:
        await client.get("https://party.invalid/")
    assert (closing.entered, closing.closed) == (1, 1)
    unopened = outbound.client("probe", timeouts=QUICK)
    await unopened.aclose()
    assert closing.closed == 2
