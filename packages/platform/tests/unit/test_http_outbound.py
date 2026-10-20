"""``OutboundHttp`` (§3.10, §10, §11): the clients a channel calls out with, untrusting and traced."""

import asyncio
import logging
import ssl
from contextlib import asynccontextmanager
from typing import Final, Self, final, override
lazy from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
lazy from pathlib import Path

import certifi
import httpx
import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from prometheus_client import CollectorRegistry
from pydantic import SecretStr

from ledger_platform.config.settings import Settings
from ledger_platform.http.outbound import (
    OUTBOUND_BODY_FAILURES,
    OUTBOUND_SECONDS,
    OutboundDeadlineError,
    OutboundHttp,
    OutboundTimeouts,
)
from ledger_platform.observability.logs import configure_logging
from ledger_platform.observability.telemetry import Telemetry
from ledger_testing.pki import Authority
lazy from ledger_testing.pki import Issued

QUICK: Final = OutboundTimeouts(connect=2.0, each=5.0, deadline=5.0)
HEAD: Final = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\n"
OK: Final = (HEAD + b"ok",)
MOVED: Final = (b"HTTP/1.1 302 Found\r\nLocation: https://elsewhere.invalid/\r\nContent-Length: 0\r\n\r\n",)
HEAD_END: Final = b"\r\n\r\n"
TAX_ID: Final = "00112345678"
PATH: Final = f"/v1/taxpayers/{TAX_ID}?rnc={TAX_ID}"
"""What a path and a query carry: a tax id, which no log line, span or metric may hold."""


@final
class Origin:
    """An HTTP/1.1 origin on 127.0.0.1 answering every request with ``chunks``, ``pause`` seconds apart.

    It counts connections and requests.
    """

    def __init__(self, chunks: Sequence[bytes], *, pause: float = 0.0) -> None:
        self.chunks = chunks
        self.pause = pause
        self.connections = 0
        self.requests = 0
        self.hung_up = asyncio.Event()
        self.port = 0

    def url(self, host: str = "127.0.0.1", scheme: str = "http") -> str:
        return f"{scheme}://{host}:{self.port}{PATH}"

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                await reader.readuntil(HEAD_END)
                self.requests += 1
                for chunk in self.chunks:
                    writer.write(chunk)
                    await writer.drain()
                    await asyncio.sleep(self.pause)
        except asyncio.IncompleteReadError, ConnectionError, ssl.SSLError:
            pass
        finally:
            writer.close()
            self.hung_up.set()


@asynccontextmanager
async def serving(origin: Origin, tls: ssl.SSLContext | None = None) -> AsyncGenerator[Origin]:
    server = await asyncio.start_server(origin.serve, "127.0.0.1", 0, ssl=tls)
    origin.port = server.sockets[0].getsockname()[1]
    try:
        yield origin
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()


@pytest.fixture
async def origin() -> AsyncIterator[Origin]:
    async with serving(Origin(OK)) as served:
        yield served


@pytest.fixture
async def moved() -> AsyncIterator[Origin]:
    async with serving(Origin(MOVED)) as served:
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

    def body_failures(self, client: str, outcome: str) -> float | None:
        return self.registry.get_sample_value(
            f"{OUTBOUND_BODY_FAILURES}_total", {"client": client, "outcome": outcome}
        )


@pytest.fixture
def traced() -> Traced:
    return Traced()


# -- TLS: a hierarchy of the test's own, and origins that present its certificates -------------------------


@final
class Pki:
    """A root, the CA file trusting it, and a server certificate's files per set of names."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.root = Authority.root()
        self.ca_file = directory / "ca.pem"
        self.ca_file.write_bytes(self.root.issued.pem())

    def server_context(self, *names: str) -> ssl.SSLContext:
        issued: Issued = self.root.server(*names)
        certificate, key = self.directory / f"{names[0]}.pem", self.directory / f"{names[0]}.key"
        certificate.write_bytes(issued.pem())
        key.write_bytes(issued.key_pem())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        context.set_alpn_protocols(["h2", "http/1.1"])  # h2 first: chosen whenever a client offers it
        return context

    def trusting(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.ca_file))


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return Pki(tmp_path_factory.mktemp("pki"))


@pytest.fixture
async def secure(pki: Pki) -> AsyncIterator[Origin]:
    async with serving(Origin(OK), pki.server_context("127.0.0.1")) as served:
        yield served


async def test_a_redirect_is_answered_as_it_came(moved: Origin, traced: Traced) -> None:
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        response = await client.get(moved.url())
    assert response.status_code == httpx.codes.FOUND
    assert moved.requests == 1


async def test_a_certificate_of_no_trusted_authority_is_refused_whatever_the_environment_says(
    secure: Origin, pki: Pki, traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbound = OutboundHttp(telemetry=traced.telemetry)
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
            await client.get(secure.url(scheme="https"))
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_file))  # what a client trusting its environment would read
    monkeypatch.setenv("SSL_CERT_DIR", str(pki.directory))
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
            await client.get(secure.url(scheme="https"))
    assert secure.requests == 0


async def test_tls_is_verified_against_the_bundled_authorities_and_speaks_http_1_1(
    secure: Origin, pki: Pki, traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(certifi, "where", lambda: str(pki.ca_file))  # the test's root as the bundle
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        response = await client.get(secure.url(scheme="https"))
    assert (response.status_code, response.http_version) == (httpx.codes.OK, "HTTP/1.1")
    assert secure.requests == 1  # the origin prefers h2 (ALPN): a client offering it would speak HTTP/2


@pytest.mark.parametrize(("keep_alive", "connections"), [(True, 1), (False, 2)])
async def test_without_keep_alive_each_request_has_a_connection_of_its_own(
    origin: Origin, traced: Traced, *, keep_alive: bool, connections: int
) -> None:
    outbound = OutboundHttp(telemetry=traced.telemetry)
    async with outbound.client("probe", timeouts=QUICK, keep_alive=keep_alive) as client:
        for _ in range(2):
            assert (await client.get(origin.url())).status_code == httpx.codes.OK
    assert (origin.requests, origin.connections) == (2, connections)


async def test_closing_a_client_closes_the_connections_it_kept(origin: Origin, traced: Traced) -> None:
    client = OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK)
    assert (await client.get(origin.url())).status_code == httpx.codes.OK
    assert not origin.hung_up.is_set()  # kept for another request
    await client.aclose()
    async with asyncio.timeout(5):
        await origin.hung_up.wait()


@final
class Shared(httpx.MockTransport):
    """Answers 202 and counts how often a client entered or closed it."""

    def __init__(self) -> None:
        super().__init__(self.answer)
        self.hosts: list[str] = []
        self.entered = 0
        self.closed = 0

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        return httpx.Response(httpx.codes.ACCEPTED)

    @override
    async def __aenter__(self) -> Self:
        self.entered += 1
        return self

    @override
    async def aclose(self) -> None:
        self.closed += 1


async def test_a_registered_transport_replaces_the_network_for_its_names_and_stays_the_registrant_s(
    traced: Traced,
) -> None:
    shared = Shared()
    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"dgii": shared, "buyers": shared})
    assert outbound.replaced("dgii")
    assert not outbound.replaced("probe")
    for name in ("dgii", "buyers", "dgii"):
        async with outbound.client(name, timeouts=QUICK) as client:
            response = await client.get("https://ecf.dgii.gov.do/testecf/x")
        assert response.status_code == httpx.codes.ACCEPTED
    await outbound.client("buyers", timeouts=QUICK).aclose()
    assert shared.hosts == ["ecf.dgii.gov.do"] * 3
    assert (shared.entered, shared.closed) == (0, 0)  # opened and closed by whoever registered it


async def test_each_step_is_bounded_by_the_client_s_timeouts(traced: Traced) -> None:
    timeouts = OutboundTimeouts(connect=1.5, each=7.0, deadline=30.0)
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=timeouts) as client:
        assert client.timeout == httpx.Timeout(7.0, connect=1.5)
        assert not client.follow_redirects
        assert not client.trust_env


async def test_an_answer_s_head_arriving_past_the_deadline_is_cut_off(traced: Traced) -> None:
    dripping = Origin(tuple(bytes([byte]) for byte in HEAD), pause=0.02)  # each byte well within `each`
    timeouts = OutboundTimeouts(connect=2.0, each=5.0, deadline=0.3)
    outbound = OutboundHttp(telemetry=traced.telemetry)
    async with serving(dripping) as served, outbound.client("probe", timeouts=timeouts) as client:
        with pytest.raises(OutboundDeadlineError, match=r"0\.3 s") as caught:
            await client.get(served.url())
    assert isinstance(caught.value, httpx.TimeoutException)  # a transport error, as httpx's own timeouts
    assert TAX_ID not in str(caught.value)
    assert traced.observed("probe", "ledger_platform.http.outbound.OutboundDeadlineError") == 1


async def test_a_body_is_the_caller_s_to_bound_once_the_head_came_in_time(traced: Traced) -> None:
    slow_body = Origin((HEAD, b"o", b"k"), pause=0.2)
    timeouts = OutboundTimeouts(connect=2.0, each=5.0, deadline=0.3)
    outbound = OutboundHttp(telemetry=traced.telemetry)
    async with serving(slow_body) as served, outbound.client("probe", timeouts=timeouts) as client:
        response = await client.get(served.url())
    assert response.content == b"ok"


async def test_a_request_is_a_client_span_naming_its_host_and_status_never_its_path(
    origin: Origin, traced: Traced
) -> None:
    async with OutboundHttp(telemetry=traced.telemetry).client("probe", timeouts=QUICK) as client:
        await client.get(origin.url())
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


async def test_a_request_to_a_pinned_address_names_the_host_it_was_meant_for(traced: Traced) -> None:
    outbound = OutboundHttp(
        telemetry=traced.telemetry,
        transports={"buyers": httpx.MockTransport(lambda _: httpx.Response(httpx.codes.OK))},
    )
    async with outbound.client("buyers", timeouts=QUICK, keep_alive=False) as client:
        await client.get(
            "https://192.0.2.10/fe", headers={"Host": "buyer.test"}, extensions={"sni_hostname": "buyer.test"}
        )
    [span] = traced.spans.get_finished_spans()
    assert (span.attributes or {})["server.address"] == "buyer.test"


@pytest.mark.parametrize(("name", "accepted"), [("buyer.test", True), ("other.test", False)])
async def test_tls_to_a_pinned_address_is_verified_for_the_name_asked_for(
    pki: Pki, traced: Traced, *, name: str, accepted: bool
) -> None:
    trusting = httpx.AsyncHTTPTransport(verify=pki.trusting(), trust_env=False)
    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"buyers": trusting})
    async with (
        serving(Origin(OK), pki.server_context("buyer.test")) as served,
        outbound.client("buyers", timeouts=QUICK, keep_alive=False) as client,
    ):
        request = client.build_request(
            "GET", served.url(scheme="https"), headers={"Host": name}, extensions={"sni_hostname": name}
        )
        if accepted:
            assert (await client.send(request)).status_code == httpx.codes.OK
        else:
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.send(request)
    await trusting.aclose()


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
        quoting = f"connection refused to {TAX_ID}"
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


async def test_a_transport_s_own_timeout_is_not_taken_for_the_deadline(traced: Traced) -> None:
    def expire(request: httpx.Request) -> httpx.Response:
        del request
        raise TimeoutError

    outbound = OutboundHttp(telemetry=traced.telemetry, transports={"probe": httpx.MockTransport(expire)})
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(TimeoutError) as caught:
            await client.get("https://party.invalid/")
    assert not isinstance(caught.value, OutboundDeadlineError)
    assert traced.observed("probe", "TimeoutError") == 1


@final
class Broken(httpx.AsyncByteStream):
    """A body that breaks after its first chunk."""

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"o"
        message = "the connection was reset"
        raise httpx.ReadError(message)


async def test_a_body_that_breaks_is_counted_by_its_error_type(traced: Traced) -> None:
    outbound = OutboundHttp(
        telemetry=traced.telemetry,
        transports={"probe": httpx.MockTransport(lambda _: httpx.Response(httpx.codes.OK, stream=Broken()))},
    )
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(httpx.ReadError):
            await client.get("https://party.invalid/")
    assert traced.observed("probe", "2xx") == 1  # the head came: the request is measured as answered
    assert traced.body_failures("probe", "httpx.ReadError") == 1


@final
class SyncOnly(httpx.SyncByteStream):
    """A body only a synchronous client can read: httpx's to refuse in an async one."""

    @override
    def __iter__(self) -> Iterator[bytes]:
        yield b"ok"


async def test_a_body_of_another_kind_is_handed_on_as_it_came(traced: Traced) -> None:
    outbound = OutboundHttp(
        telemetry=traced.telemetry,
        transports={
            "probe": httpx.MockTransport(lambda _: httpx.Response(httpx.codes.OK, stream=SyncOnly()))
        },
    )
    async with outbound.client("probe", timeouts=QUICK) as client:
        with pytest.raises(AssertionError):  # httpx's own check that an async transport answers async bodies
            await client.get("https://party.invalid/")


async def test_clients_of_one_registry_share_its_histogram_whose_buckets_reach_a_minute(
    origin: Origin, traced: Traced
) -> None:
    for name in ("dgii", "buyers"):
        async with OutboundHttp(telemetry=traced.telemetry).client(name, timeouts=QUICK) as client:
            await client.get(origin.url())
    assert (traced.observed("dgii", "2xx"), traced.observed("buyers", "2xx")) == (1, 1)
    labels = {"client": "dgii", "outcome": "2xx"}
    assert traced.registry.get_sample_value(f"{OUTBOUND_SECONDS}_bucket", labels | {"le": "60.0"}) == 1


@final
class Kept(logging.Handler):
    """Keeps every record that reaches the root logger."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def kept() -> Iterator[Kept]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    configure_logging(
        Settings(environment="test", database_url=SecretStr("postgresql+psycopg://a:b@db.invalid/l")),
        service="probe",
    )
    handler = Kept()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


async def test_no_log_line_holds_a_request_s_path_or_query(kept: Kept, traced: Traced) -> None:
    outbound = OutboundHttp(
        telemetry=traced.telemetry,
        transports={"probe": httpx.MockTransport(lambda _: httpx.Response(httpx.codes.OK))},
    )
    async with outbound.client("probe", timeouts=QUICK) as client:
        await client.get(f"https://party.invalid{PATH}")
    assert [record.getMessage() for record in kept.records if TAX_ID in record.getMessage()] == []
