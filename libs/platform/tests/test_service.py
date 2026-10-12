import asyncio
import contextlib
import logging
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from functools import partial
from math import inf, nan
from typing import Annotated, Final, Protocol, TypedDict, final, override
from urllib.parse import urlsplit
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

import grpc
import httpx
import pytest
from fastapi import APIRouter, Depends
from nats.js.errors import NotFoundError
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace import TracerProvider
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import Text, literal_column, select
from structlog.testing import capture_logs
lazy from nats.aio.client import Client
lazy from nats.js import JetStreamContext
lazy from opentelemetry.proto.common.v1.common_pb2 import KeyValue
lazy from opentelemetry.proto.trace.v1.trace_pb2 import Span
lazy from prometheus_client import CollectorRegistry

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_platform.consumer import ConsumerRefusedError, ConsumerSpec
from ledger_platform.database import DatabaseLimits
from ledger_platform.grpc_server import GrpcBindError, serve_grpc
from ledger_platform.outbox import DEFAULT_MAX_PAYLOAD, MAX_PUBLISH_TIMEOUT, EventTooLargeError
from ledger_platform.service import (
    Events,
    Interfaces,
    InvalidServiceOptionError,
    Service,
    ServiceAlreadyStartedError,
    ServiceLimits,
    ServiceNotRunningError,
)
from ledger_platform.settings import ServiceSettings
from ledger_platform.streams import StreamSpec
lazy from ledger_platform.consumer import Handler
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.service import Runtime

STREAM: Final = StreamSpec(name="SERVICE_TEST", subjects=("service-test.>",))
SUBJECT: Final = "service-test.paid"
LEDGER: Final = "payments-ledger"
AUDIT: Final = "payments-audit"
SECRET: Final = "Key (tax_id)=(001-1234567-8) already exists"
"""What a database error says: personal data that must never leave the process in a span."""
RNC: Final = "00112345678"
"""A national id, as a client may put it in a query string."""
TRACE_ID: Final = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT: Final = f"00-{TRACE_ID}-b7ad6b7169203331-01"
TRACE_SERVICE: Final = "opentelemetry.proto.collector.trace.v1.TraceService"
ALL_READY: Final = {"database": True, "nats": True, "workers": True}
TIMEOUT: Final = 5.0
WINDING_DOWN: Final = 0.3
"""Seconds a held handler takes to stop once cancelled: a stop that did not wait for it would not see it."""

type Context = grpc.aio.ServicerContext[bytes, bytes]


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


class Paid(TypedDict):
    id: str


class Problem(TypedDict):
    code: str


READINESS: Final = TypeAdapter(Readiness)
PAID: Final = TypeAdapter(Paid)
PROBLEM: Final = TypeAdapter(Problem)
ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])
"""An internet address as a socket reports it: host and port, plus flow and scope for IPv6."""


class PaymentsSettings(ServiceSettings):
    """The settings of a service of its own: the chassis's, and one more."""

    currency: str = "DOP"


class SettlementError(Exception):
    """What a service's code raises with a message that must never leave the process."""


class Subscribing(Protocol):
    """A NATS connection as ``silent`` uses it: nats-py leaves the type of ``subscribe``'s ``future`` open."""

    async def subscribe(self, subject: str) -> object: ...


def paid() -> Envelope:
    return Envelope.wrap(Money(amount="5", currency="DOP"), source="payments", tenant=None)


def configured(
    database_url: str, nats_url: str, *, grpc_port: int = 0, otlp_endpoint: str | None = None
) -> ServiceSettings:
    return ServiceSettings(
        service_name="payments",
        environment="test",
        database_url=SecretStr(database_url),
        nats_url=nats_url,
        grpc_port=grpc_port,
        otlp_endpoint=otlp_endpoint,
    )


def bare(database_url: str, nats_url: str) -> Service[ServiceSettings]:
    """A service with nothing of its own: no routes, no streams, no consumers, no gRPC."""
    return Service(
        settings=ServiceSettings(
            service_name="bare", database_url=SecretStr(database_url), nats_url=nats_url
        ),
        version="1.0",
    )


@final
class Payments:
    """A small service with a part of every kind, keeping what each part receives for the test to read.

    ``POST /payments`` stores an event; ``GET /settle`` fails inside a span of its own. The consumers named
    in ``consumers`` keep what they handle (or, when ``hold``, wait for ``release`` first, and take a moment
    to wind down when they are cancelled meanwhile). Its gRPC methods: ``Ping`` echoes, ``Slow`` waits for
    ``release`` and then uses the database and NATS.
    """

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        consumers: Sequence[str] = (LEDGER,),
        orphan: bool = False,
        hold: bool = False,
        limits: ServiceLimits | None = None,
    ) -> None:
        self.received: dict[str, list[Envelope]] = {name: [] for name in consumers}
        self.handling = asyncio.Event()
        self.slow_started = asyncio.Event()
        self.release = asyncio.Event()
        self._hold = hold
        specs = [
            ConsumerSpec(name=name, stream=STREAM.name, subject="service-test.>", handler=self._keeper(name))
            for name in consumers
        ]
        if orphan:  # a consumer of a stream nobody declared: JetStream refuses it, and its worker stops
            specs.append(ConsumerSpec(name="orphan", stream="NOWHERE", subject="nowhere.>", handler=ignore))
        self._specs = tuple(specs)
        self.service = Service(
            settings=settings,
            version="1.0",
            interfaces=Interfaces(http=(self._routes(),), grpc=self._register),
            events=Events(streams=(STREAM,), consumers=self._consumers),
            limits=ServiceLimits() if limits is None else limits,
        )

    async def got(self, consumer: str, count: int) -> bool:
        return len(self.received[consumer]) >= count

    async def _runtime(self) -> Runtime[ServiceSettings]:
        return self.service.runtime()

    def _routes(self) -> APIRouter:
        router = APIRouter()

        @router.post("/payments", status_code=202)
        async def pay(runtime: Annotated[Runtime[ServiceSettings], Depends(self._runtime)]) -> Paid:
            envelope = paid()
            async with runtime.database.transaction(None) as unit:
                await runtime.outbox.add(unit, envelope, subject=SUBJECT)
            return {"id": str(envelope.id)}

        @router.get("/settle")
        async def settle(
            runtime: Annotated[Runtime[ServiceSettings], Depends(self._runtime)], rnc: str
        ) -> None:
            del rnc
            with runtime.tracing.tracer.start_as_current_span("settle"):
                raise SettlementError(SECRET)

        return router

    def _keeper(self, consumer: str) -> Handler:
        async def keep(envelope: Envelope, unit: UnitOfWork) -> None:
            del unit
            if self._hold:
                self.handling.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(WINDING_DOWN)
                    raise
            self.received[consumer].append(envelope)

        return keep

    def _consumers(self, runtime: Runtime[ServiceSettings]) -> Sequence[ConsumerSpec]:
        del runtime
        return self._specs

    def _register(self, runtime: Runtime[ServiceSettings], server: grpc.aio.Server) -> None:
        async def ping(request: bytes, context: Context) -> bytes:
            del context
            return request

        async def slow(request: bytes, context: Context) -> bytes:
            del context
            self.slow_started.set()
            await self.release.wait()
            await runtime.database.ping()
            await runtime.jetstream.stream_info(STREAM.name)
            return request

        methods = {
            "Ping": grpc.unary_unary_rpc_method_handler(
                ping, request_deserializer=bytes, response_serializer=bytes
            ),
            "Slow": grpc.unary_unary_rpc_method_handler(
                slow, request_deserializer=bytes, response_serializer=bytes
            ),
        }
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Payments", methods),))


async def ignore(envelope: Envelope, unit: UnitOfWork) -> None:
    del envelope, unit


@final
class Proxy:
    """A TCP relay to NATS that a test can cut: the connections through it drop, and new ones are closed at
    once, until it is restored. NATS itself, and what JetStream stores, are untouched."""

    def __init__(self, host: str, port: int) -> None:
        self._upstream = (host, port)
        self._open = True
        self._writers: set[asyncio.StreamWriter] = set()
        self.url = ""

    @classmethod
    @asynccontextmanager
    async def to(cls, nats_url: str) -> AsyncGenerator[Proxy]:
        upstream = urlsplit(nats_url)
        assert upstream.hostname is not None
        assert upstream.port is not None
        proxy = cls(upstream.hostname, upstream.port)
        server = await asyncio.start_server(proxy.accept, "127.0.0.1", 0)
        proxy.url = f"nats://127.0.0.1:{ADDRESS.validate_python(server.sockets[0].getsockname())[1]}"
        try:
            yield proxy
        finally:
            proxy.cut()
            server.close()
            await server.wait_closed()

    async def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self._open:
            writer.close()
            return
        upstream_reader, upstream_writer = await asyncio.open_connection(*self._upstream)
        self._writers |= {writer, upstream_writer}
        await asyncio.gather(relay(reader, upstream_writer), relay(upstream_reader, writer))

    def cut(self) -> None:
        self._open = False
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    def restore(self) -> None:
        self._open = True


async def relay(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError):
        while chunk := await source.read(65536):
            sink.write(chunk)
            await sink.drain()
    sink.close()


@final
class Collector:
    """An OpenTelemetry collector as far as an OTLP/gRPC exporter can tell: what it is sent, kept."""

    def __init__(self) -> None:
        self.requests: list[ExportTraceServiceRequest] = []

    def register(self, server: grpc.aio.Server) -> None:
        export = grpc.unary_unary_rpc_method_handler(
            self._export,
            request_deserializer=ExportTraceServiceRequest.FromString,
            response_serializer=ExportTraceServiceResponse.SerializeToString,
        )
        server.add_generic_rpc_handlers(
            (grpc.method_handlers_generic_handler(TRACE_SERVICE, {"Export": export}),)
        )

    def spans(self) -> dict[str, Span]:
        return {
            span.name: span
            for request in self.requests
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        }

    async def _export(
        self,
        request: ExportTraceServiceRequest,
        context: grpc.aio.ServicerContext[ExportTraceServiceRequest, ExportTraceServiceResponse],
    ) -> ExportTraceServiceResponse:
        del context
        self.requests.append(request)
        return ExportTraceServiceResponse()


@final
class Recording(logging.Handler):
    """Keeps the records of the standard-library loggers it is attached to."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def texts(attributes: Sequence[KeyValue]) -> dict[str, str]:
    return {attribute.key: attribute.value.string_value for attribute in attributes}


@asynccontextmanager
async def running[S: ServiceSettings](service: Service[S]) -> AsyncGenerator[httpx.AsyncClient]:
    """The service started, and a client of its HTTP app; stopped on exit."""
    transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
    async with (
        service.app.router.lifespan_context(service.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


async def readiness(client: httpx.AsyncClient) -> tuple[int, dict[str, bool]]:
    response = await client.get("/readyz")
    return response.status_code, READINESS.validate_json(response.content)["checks"]


async def nats_is(client: httpx.AsyncClient, *, connected: bool) -> bool:
    return (await readiness(client))[1]["nats"] is connected


async def handled(registry: CollectorRegistry, consumer: str) -> bool:
    """Whether ``consumer`` has handled an event: counted once its transaction committed."""
    return registry.get_sample_value("ledger_events_total", {"consumer": consumer, "outcome": "handled"}) == 1


async def bound(jetstream: JetStreamContext, consumer: str) -> bool:
    """Whether the durable consumer exists in JetStream: its worker has made it, and pulls from it next."""
    try:
        await jetstream.consumer_info(STREAM.name, consumer)
    except NotFoundError:
        return False
    return True


async def within(seconds: float, check: Callable[[], Awaitable[bool]]) -> None:
    """Return as soon as ``check`` holds; fail if it does not within ``seconds``."""
    async with asyncio.timeout(seconds):
        while True:
            if await check():
                return
            await asyncio.sleep(0.05)


async def call(port: int, method: str, request: bytes = b"ok") -> bytes:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = channel.unary_unary(
            f"/test.Payments/{method}", request_serializer=bytes, response_deserializer=bytes
        )
        return await stub(request, timeout=TIMEOUT * 2)


def running_port(service: Service[ServiceSettings]) -> int:
    port = service.grpc_port
    assert port is not None
    return port


async def silent(connection: Subscribing, subject: str) -> None:
    """Listen on ``subject`` and never answer: a JetStream publish to it waits for its acknowledgement."""
    await connection.subscribe(subject)


def occupy_a_port() -> socket.socket:
    """A listening socket on every interface, the way grpc's ``[::]`` listens: nobody else binds its port."""
    if socket.has_dualstack_ipv6():
        return socket.create_server(("::", 0), family=socket.AF_INET6, dualstack_ipv6=True)
    return socket.create_server(("", 0))


async def session_setting(database: Database, name: str) -> str:
    async with database.transaction(None) as unit:
        shown = literal_column(f"current_setting('{name}')", Text)
        return (await unit.connection.execute(select(shown))).scalar_one()


@pytest.fixture
def infrastructure(database: Database, jetstream: JetStreamContext) -> JetStreamContext:
    """The chassis's tables, created for the test and dropped after it, and JetStream, whose streams (and
    durable consumers) are deleted after it."""
    del database
    return jetstream


async def test_a_request_s_event_reaches_a_consumer_through_the_whole_chassis(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    payments = Payments(configured(database_url, nats_url))
    with capture_logs() as logs:
        async with running(payments.service) as client:
            assert await readiness(client) == (200, ALL_READY)
            streams = {info.config.name for info in await infrastructure.streams_info()}
            assert streams == {"DEAD_LETTERS", STREAM.name}

            response = await client.post("/payments")
            assert response.status_code == 202
            await within(TIMEOUT, partial(payments.got, LEDGER, 1))
            assert str(payments.received[LEDGER][0].id) == PAID.validate_json(response.content)["id"]

            assert await call(running_port(payments.service), "Ping") == b"ok"
            connection = payments.service.runtime().nats

    assert connection.is_closed
    assert [entry for entry in logs if entry["log_level"] in {"warning", "error"}] == []  # a stop is no alarm
    workers = payments.service.workers
    assert [worker.get_name() for worker in workers] == ["outbox relay", f"consumer {LEDGER}"]
    assert all(worker.done() for worker in workers)
    assert payments.service.grpc_port is None
    with pytest.raises(ServiceNotRunningError, match=r"^the service is not running$"):
        payments.service.runtime()


async def test_before_it_starts_the_service_is_not_ready_and_its_routes_answer_503(
    database_url: str, nats_url: str
) -> None:
    payments = Payments(configured(database_url, nats_url))
    transport = httpx.ASGITransport(app=payments.service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert await readiness(client) == (503, {"database": False, "nats": False, "workers": False})
        response = await client.post("/payments")
    assert (response.status_code, PROBLEM.validate_json(response.content)["code"]) == (
        503,
        "service_not_running",
    )


async def test_a_database_that_refuses_the_service_makes_it_not_ready(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    refused = database_url.replace(":app-password@", ":wrong-password@")
    assert refused != database_url
    service = bare(refused, nats_url)
    async with running(service) as client:
        assert await readiness(client) == (503, {"database": False, "nats": True, "workers": True})


async def test_a_worker_that_stops_makes_the_service_not_ready(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    payments = Payments(configured(database_url, nats_url))
    async with running(payments.service) as client:
        relay = payments.service.workers[0]
        relay.cancel()
        await asyncio.wait((relay,))
        assert await readiness(client) == (503, {"database": True, "nats": True, "workers": False})


async def test_a_worker_that_fails_is_logged_with_its_error_and_keeps_the_service_not_ready(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    payments = Payments(configured(database_url, nats_url), orphan=True)
    with capture_logs() as logs:
        async with running(payments.service) as client:
            orphan = next(
                worker for worker in payments.service.workers if worker.get_name() == "consumer orphan"
            )
            await asyncio.wait((orphan,))
            assert (await readiness(client))[1]["workers"] is False
    (failure,) = [entry for entry in logs if entry["event"] == "service.worker_failed"]
    assert isinstance(failure.pop("exc_info"), ConsumerRefusedError)
    assert failure == {"event": "service.worker_failed", "log_level": "error", "worker": "consumer orphan"}


async def test_without_otlp_and_grpc_the_service_still_runs(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    service = bare(database_url, nats_url)
    async with running(service) as client:
        assert service.grpc_port is None
        assert service.runtime().settings.service_name == "bare"
        assert [worker.get_name() for worker in service.workers] == ["outbox relay"]
        assert await readiness(client) == (200, ALL_READY)


async def test_the_runtime_holds_the_settings_the_service_was_given_with_their_own_type(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """A service's own settings reach its code with their own type: the type checkers know ``currency``."""
    del infrastructure
    settings = PaymentsSettings(
        service_name="payments", database_url=SecretStr(database_url), nats_url=nats_url, currency="USD"
    )
    service = Service(settings=settings, version="1.0")
    async with running(service):
        assert service.runtime().settings is settings
        assert service.runtime().settings.currency == "USD"


async def test_a_service_starts_once(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Its metrics and its spans belong to the one run: a second start would register them twice."""
    del infrastructure
    service = bare(database_url, nats_url)
    async with running(service):
        pass
    with pytest.raises(
        ServiceAlreadyStartedError, match=r"^a service starts once: build a new one to start again$"
    ):
        async with running(service):
            pass


async def test_a_connection_to_nats_closed_while_running_is_reported_and_the_service_still_stops(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Draining a closed connection raises: the stop must not try."""
    del infrastructure
    payments = Payments(configured(database_url, nats_url))
    async with running(payments.service) as client:
        await payments.service.runtime().nats.close()
        status, checks = await readiness(client)
        assert (status, checks["nats"], checks["database"]) == (503, False, True)


async def test_the_relay_and_the_consumers_resume_when_nats_comes_back(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    async with Proxy.to(nats_url) as proxy:
        payments = Payments(configured(database_url, proxy.url))
        with capture_logs() as logs:
            async with running(payments.service) as client:
                options = payments.service.runtime().nats.options
                assert (options["max_reconnect_attempts"], options["reconnect_time_wait"]) == (-1, 2)
                await within(TIMEOUT, partial(bound, infrastructure, LEDGER))
                proxy.cut()
                await within(TIMEOUT, partial(nats_is, client, connected=False))
                response = await client.post("/payments")
                assert response.status_code == 202

                proxy.restore()
                await within(4 * TIMEOUT, partial(payments.got, LEDGER, 1))
                assert await readiness(client) == (200, ALL_READY)
    (envelope,) = payments.received[LEDGER]
    assert str(envelope.id) == PAID.validate_json(response.content)["id"]
    events = [entry["event"] for entry in logs]
    assert events.index("nats.disconnected") < events.index("nats.reconnected")


async def test_a_service_whose_nats_is_unreachable_still_stops(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """A connection still trying to reconnect cannot drain: the stop closes it, and it stops trying."""
    del infrastructure
    async with Proxy.to(nats_url) as proxy:
        payments = Payments(configured(database_url, proxy.url))
        async with asyncio.timeout(4 * TIMEOUT), running(payments.service) as client:
            connection = payments.service.runtime().nats
            proxy.cut()
            await within(TIMEOUT, partial(nats_is, client, connected=False))
    assert connection.is_closed


async def test_a_service_started_before_nats_is_reachable_waits_for_it(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The first connection is retried like any other, each attempt logged, instead of failing the start."""
    del infrastructure
    async with Proxy.to(nats_url) as proxy:
        proxy.cut()
        payments = Payments(configured(database_url, proxy.url))
        asyncio.get_running_loop().call_later(TIMEOUT / 5, proxy.restore)
        with capture_logs() as logs:
            async with asyncio.timeout(4 * TIMEOUT), running(payments.service) as client:
                assert await readiness(client) == (200, ALL_READY)
    (attempt, *_) = [entry for entry in logs if entry["event"] == "nats.error"]
    assert set(attempt) == {"event", "log_level", "error", "detail"}
    assert attempt["log_level"] == "warning"


@pytest.mark.parametrize("publish_timeout", [0.0, -1.0, nan, inf, MAX_PUBLISH_TIMEOUT + 1])
def test_a_publish_timeout_the_relay_cannot_wait_is_refused(publish_timeout: float) -> None:
    with pytest.raises(InvalidServiceOptionError, match=r"^service option publish_timeout must be"):
        ServiceLimits(publish_timeout=publish_timeout)


@pytest.mark.parametrize("publish_timeout", [5.0, 6.0])
def test_a_publish_timeout_the_database_would_cut_short_is_refused(publish_timeout: float) -> None:
    """The relay waits for JetStream inside its transaction: PostgreSQL would end a session idle that long."""
    database = DatabaseLimits(idle_in_transaction_session_timeout=timedelta(seconds=5))
    with pytest.raises(
        InvalidServiceOptionError,
        match=(
            r"^service option publish_timeout must be below the database's "
            r"idle_in_transaction_session_timeout \(5\.0 seconds\), not float: "
        ),
    ):
        ServiceLimits(database=database, publish_timeout=publish_timeout)


def test_a_publish_timeout_below_the_database_s_idle_limit_is_accepted() -> None:
    database = DatabaseLimits(idle_in_transaction_session_timeout=timedelta(seconds=5))
    assert ServiceLimits(database=database, publish_timeout=4.999).publish_timeout == 4.999


async def test_the_service_s_name_and_limits_reach_postgresql(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    database = DatabaseLimits(
        statement_timeout=timedelta(seconds=7),
        lock_timeout=timedelta(seconds=3),
        idle_in_transaction_session_timeout=timedelta(seconds=40),
    )
    payments = Payments(configured(database_url, nats_url), limits=ServiceLimits(database=database))
    async with running(payments.service):
        used = payments.service.runtime().database
        assert await session_setting(used, "application_name") == "payments"
        assert await session_setting(used, "statement_timeout") == "7s"
        assert await session_setting(used, "lock_timeout") == "3s"
        assert await session_setting(used, "idle_in_transaction_session_timeout") == "40s"


async def test_the_relay_waits_for_jetstream_as_long_as_the_limits_say(
    database_url: str,
    nats_url: str,
    infrastructure: JetStreamContext,
    connect: Callable[[], Awaitable[Client]],
) -> None:
    """The default would wait five seconds before giving up on a publish nobody acknowledges."""
    del infrastructure
    await silent(await connect(), "unacknowledged.paid")
    payments = Payments(configured(database_url, nats_url), limits=ServiceLimits(publish_timeout=0.2))
    async with running(payments.service):
        runtime = payments.service.runtime()
        with capture_logs() as logs:
            async with runtime.database.transaction(None) as unit:
                await runtime.outbox.add(unit, paid(), subject="unacknowledged.paid")

            async def gave_up() -> bool:
                return any(entry["event"] == "outbox.publish_failed" for entry in logs)

            await within(TIMEOUT / 2, gave_up)


async def test_events_are_refused_above_the_payload_limit_nats_announces(
    database_url: str, small_payload_nats_url: str, database: Database
) -> None:
    del database
    payments = Payments(configured(database_url, small_payload_nats_url))
    async with running(payments.service):
        runtime = payments.service.runtime()
        limit = runtime.nats.max_payload
        assert limit < DEFAULT_MAX_PAYLOAD
        too_large = replace(paid(), data=b"x" * limit)
        async with runtime.database.transaction(None) as unit:
            with pytest.raises(EventTooLargeError, match=f"NATS accepts at most {limit}$"):
                await runtime.outbox.add(unit, too_large, subject=SUBJECT)


async def test_every_consumer_gets_the_event_and_is_counted_under_its_own_name(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    payments = Payments(configured(database_url, nats_url), consumers=(LEDGER, AUDIT))
    async with running(payments.service) as client:
        registry = payments.service.runtime().registry
        await client.post("/payments")
        for consumer in (LEDGER, AUDIT):
            await within(TIMEOUT, partial(handled, registry, consumer))
    assert [len(payments.received[consumer]) for consumer in (LEDGER, AUDIT)] == [1, 1]


async def test_an_event_carries_the_trace_of_the_request_that_stored_it(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    payments = Payments(configured(database_url, nats_url))
    async with running(payments.service) as client:
        await client.post("/payments", headers={"traceparent": TRACEPARENT})
        await within(TIMEOUT, partial(payments.got, LEDGER, 1))
    (envelope,) = payments.received[LEDGER]
    assert envelope.traceparent is not None
    assert envelope.traceparent.split("-")[1] == TRACE_ID


async def test_spans_reach_the_collector_without_exception_messages_or_query_strings(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    collector = Collector()
    async with serve_grpc(port=0, register=collector.register) as port:
        payments = Payments(configured(database_url, nats_url, otlp_endpoint=f"http://127.0.0.1:{port}"))
        async with running(payments.service) as client:
            failed = await client.get("/settle", params={"rnc": RNC})
            assert failed.status_code == 500
        # stopping the service exported what was pending, to the collector that is still listening
    exported = "".join(str(request) for request in collector.requests)
    assert SECRET not in exported
    assert RNC not in exported
    spans = collector.spans()
    request, settling = spans["GET /settle"], spans["settle"]
    assert texts(request.attributes)["error.type"] == f"{SettlementError.__module__}.SettlementError"
    assert "url.query" not in texts(request.attributes)
    assert (request.status.message, settling.status.message) == ("", "")
    (event,) = settling.events
    assert texts(event.attributes)["exception.type"] == f"{SettlementError.__module__}.SettlementError"
    resources = [texts(each.resource.attributes) for each in collector.requests[0].resource_spans]
    assert all(resource["service.name"] == "payments" for resource in resources)


async def test_two_services_of_one_process_start_without_an_opentelemetry_warning(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The first sets the process-wide tracer provider, unless a test did before; the second leaves it."""
    del infrastructure
    recording = Recording()
    opentelemetry = logging.getLogger("opentelemetry")
    opentelemetry.addHandler(recording)
    try:
        for service in (bare(database_url, nats_url), bare(database_url, nats_url)):
            async with running(service):
                pass
    finally:
        opentelemetry.removeHandler(recording)
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    assert recording.records == []


async def test_a_call_in_flight_when_the_service_stops_finishes_with_the_database_and_nats(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The gRPC server stops first and waits for the calls in flight, while all they need still runs."""
    del infrastructure
    payments = Payments(configured(database_url, nats_url))
    async with running(payments.service):
        in_flight = asyncio.create_task(call(running_port(payments.service), "Slow", b"done"))
        await payments.slow_started.wait()
        asyncio.get_running_loop().call_later(0.2, payments.release.set)
    assert await in_flight == b"done"


async def test_a_delivery_in_flight_when_the_service_stops_goes_back_to_jetstream_at_once(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The consumers stop, and are waited for, before NATS drains: the handling is rolled back and the message
    given back, so the next replica gets it now, not after the consumer's ``ack_wait`` (seventy seconds by
    default). The handler takes a moment to wind down: a stop that did not wait would drain NATS under it."""
    del infrastructure
    stopping = Payments(configured(database_url, nats_url), hold=True)
    async with running(stopping.service) as client:
        await client.post("/payments")
        async with asyncio.timeout(TIMEOUT):
            await stopping.handling.wait()
    next_replica = Payments(configured(database_url, nats_url))
    async with running(next_replica.service):
        await within(TIMEOUT, partial(next_replica.got, LEDGER, 1))
    assert stopping.received[LEDGER] == []


async def test_a_start_that_fails_halfway_releases_what_had_started(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    with occupy_a_port() as taken:
        port = ADDRESS.validate_python(taken.getsockname())[1]
        payments = Payments(configured(database_url, nats_url, grpc_port=port))
        with pytest.raises(GrpcBindError):
            async with running(payments.service):
                pass
    workers = payments.service.workers
    assert [worker.get_name() for worker in workers] == ["outbox relay", f"consumer {LEDGER}"]
    assert all(worker.cancelled() for worker in workers)
    with pytest.raises(ServiceNotRunningError):
        payments.service.runtime()
