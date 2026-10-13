import asyncio
import contextlib
import inspect
import logging
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from math import inf, nan
from time import monotonic
from typing import Annotated, Final, Literal, Never, Protocol, TypedDict, final, override
from urllib.parse import urlsplit
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, MutableMapping, Sequence
lazy from collections.abc import Set as AbstractSet

import grpc
import httpx
import pytest
from fastapi import APIRouter, Depends
from nats.js.api import AckPolicy, ConsumerConfig
from nats.js.errors import NotFoundError
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry, Gauge
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import Text, literal_column, select
from sqlalchemy.pool import QueuePool
from structlog.testing import capture_logs
lazy from nats.aio.client import Client
lazy from nats.js import JetStreamContext
lazy from opentelemetry.proto.common.v1.common_pb2 import KeyValue
lazy from opentelemetry.proto.trace.v1.trace_pb2 import Span

from ledger.common.v1.money_pb2 import Money
from ledger_contracts.envelope import Envelope
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_platform.authorization import CachedPermissions, PermissionSource
from ledger_platform.consumer import ConsumerRefusedError, ConsumerSpec
from ledger_platform.database import Database, DatabaseLimits
from ledger_platform.grpc_server import GrpcBindError, serve_grpc
from ledger_platform.health import DuplicateCheckError
from ledger_platform.identity_client import PermissionCache, follow_permission_changes
from ledger_platform.outbox import DEFAULT_MAX_PAYLOAD, EventTooLargeError, InvalidOutboxOptionError
from ledger_platform.service import (
    ABANDONED_CLOSES,
    Events,
    Interfaces,
    InvalidServiceOptionError,
    NatsUnreachableError,
    Service,
    ServiceAlreadyStartedError,
    ServiceLimits,
    ServiceNotRunningError,
    UnexpectedResourcesError,
)
from ledger_platform.settings import ServiceSettings
from ledger_platform.streams import StreamSpec, ensure_stream
from ledger_platform.supervision import DuplicateWorkerError
lazy from ledger_platform.consumer import ConsumerApi, Handler
lazy from ledger_platform.database import UnitOfWork
lazy from ledger_platform.service import Runtime, RuntimeDependency

STREAM: Final = StreamSpec(name="SERVICE_TEST", subjects=("service-test.>",))
SUBJECT: Final = "service-test.paid"
LEDGER: Final = "payments-ledger"
AUDIT: Final = "payments-audit"
ORPHAN: Final = "orphan"
LATE: Final = "payments-late"
LATER: Final = StreamSpec(name="LATER", subjects=("later.>",))
"""A stream another service declares, which starts after this one."""
SECRET: Final = "Key (tax_id)=(001-1234567-8) already exists"
"""What a database error says: personal data that must never leave the process in a span."""
RNC: Final = "00112345678"
"""A national id, as a client may put it in a query string."""
TRACE_ID: Final = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT: Final = f"00-{TRACE_ID}-b7ad6b7169203331-01"
TRACE_SERVICE: Final = "opentelemetry.proto.collector.trace.v1.TraceService"
ALL_READY: Final = {"database": True, "nats": True, "workers": True, "schema": True}
TIMEOUT: Final = 5.0
WINDING_DOWN: Final = 0.3
"""Seconds a held handler takes to stop once cancelled: a stop that did not wait for it would not see it."""

type Context = grpc.aio.ServicerContext[bytes, bytes]
type Holding = Literal["no", "politely", "stubbornly"]
"""Whether a consumer's handler waits for ``release``, and how it takes a cancellation meanwhile."""


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


class Paid(TypedDict):
    id: str


class Problem(TypedDict):
    code: str


class Liveness(TypedDict):
    status: str
    wedged: list[str]
    dead: list[str]


READINESS: Final = TypeAdapter(Readiness)
PAID: Final = TypeAdapter(Paid)
PROBLEM: Final = TypeAdapter(Problem)
LIVENESS: Final = TypeAdapter(Liveness)
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
        nats_url=SecretStr(nats_url),
        grpc_port=grpc_port,
        otlp_endpoint=otlp_endpoint,
    )


def bare(database_url: str, nats_url: str) -> Service[ServiceSettings]:
    """A service with nothing of its own: no routes, no streams, no consumers, no gRPC."""
    return Service(
        settings=ServiceSettings(
            service_name="bare", database_url=SecretStr(database_url), nats_url=SecretStr(nats_url)
        ),
        version="1.0",
    )


def nothing() -> None:
    return None


@final
class Payments:
    """A small service with a part of every kind, keeping what each part receives for the test to read.

    ``POST /payments`` stores an event; ``GET /settle`` fails inside a span of its own. The consumers named
    in ``consumers`` keep what they handle. When ``hold``, they wait for ``release`` first and, cancelled
    meanwhile, take a moment to wind down (or, when ``stubborn``, ignore the cancellation and wait on). Its
    gRPC methods: ``Ping`` echoes, ``Slow`` waits for ``release`` and then uses the database and NATS.
    ``while_starting`` runs as the service makes its consumers, before any worker has run.
    """

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        consumers: Sequence[str] = (LEDGER,),
        hold: Holding = "no",
        while_starting: Callable[[], None] = nothing,
        limits: ServiceLimits | None = None,
    ) -> None:
        self.received: dict[str, list[Envelope]] = {name: [] for name in consumers}
        self.handling = asyncio.Event()
        self.slow_started = asyncio.Event()
        self.release = asyncio.Event()
        self._hold = hold
        self._while_starting = while_starting
        self._specs = tuple(
            # a consumer of a stream another service creates, maybe later
            ConsumerSpec(name=name, stream=LATER.name, subject="later.>", handler=self._keeper(name))
            if name == LATE
            else ConsumerSpec(
                name=name, stream=STREAM.name, subject="service-test.>", handler=self._keeper(name)
            )
            for name in consumers
        )
        self.service = Service(
            settings=settings,
            version="1.0",
            interfaces=Interfaces(http=self._routes, grpc=self._register),
            events=Events(streams=(STREAM,), consumers=self._consumers),
            limits=ServiceLimits() if limits is None else limits,
        )

    async def got(self, consumer: str, count: int) -> bool:
        return len(self.received[consumer]) >= count

    def _routes(self, runtime: RuntimeDependency[ServiceSettings]) -> Sequence[APIRouter]:
        """The routes as a service declares them: ``runtime`` is the dependency the service hands them."""
        router = APIRouter()

        @router.post("/payments", status_code=202)
        async def pay(current: Annotated[Runtime[ServiceSettings], Depends(runtime)]) -> Paid:
            envelope = paid()
            async with current.database.transaction(None) as unit:
                await current.outbox.add(unit, envelope, subject=SUBJECT)
            return {"id": str(envelope.id)}

        @router.get("/settle")
        async def settle(current: Annotated[Runtime[ServiceSettings], Depends(runtime)], rnc: str) -> None:
            del rnc
            with current.tracing.tracer.start_as_current_span("settle"):
                raise SettlementError(SECRET)

        @router.get("/taxpayers/{rnc}")
        async def taxpayer(rnc: str) -> dict[str, int]:
            return {"digits": len(rnc)}

        return (router,)

    def _keeper(self, consumer: str) -> Handler:
        async def keep(envelope: Envelope, unit: UnitOfWork) -> None:
            del unit
            if self._hold != "no":
                self.handling.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    if self._hold == "stubbornly":
                        await self.release.wait()  # as if nothing had happened, until cancelled again
                    await asyncio.sleep(WINDING_DOWN)
                    raise
            self.received[consumer].append(envelope)

        return keep

    def _consumers(self, runtime: Runtime[ServiceSettings]) -> Sequence[ConsumerSpec]:
        del runtime
        self._while_starting()
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


@final
class Proxy:
    """A TCP relay to NATS that a test can cut or hold, until it restores it. NATS itself, and what JetStream
    stores, are untouched.

    Cut, it drops the connections through it and stops listening, so that every attempt to connect is refused
    before a stream exists: nats-py does not close the stream of a reconnection attempt that fails during its
    handshake, and leaves it to the garbage collector (a ``ResourceWarning`` that would fail some later test).
    Held, it drops them and accepts new ones without ever answering.
    """

    def __init__(self, host: str, port: int) -> None:
        self._upstream = (host, port)
        self._holding = False
        self._frozen = False
        self._writers: set[asyncio.StreamWriter] = set()
        self._listening: asyncio.Server | None = None
        self._listeners: list[asyncio.Server] = []
        self.port = 0
        self.held = 0

    @classmethod
    @asynccontextmanager
    async def to(cls, nats_url: str) -> AsyncGenerator[Proxy]:
        upstream = urlsplit(nats_url)
        assert upstream.hostname is not None
        assert upstream.port is not None
        proxy = cls(upstream.hostname, upstream.port)
        await proxy.restore()
        try:
            yield proxy
        finally:
            proxy.cut()
            for listener in proxy._listeners:
                await listener.wait_closed()

    @property
    def url(self) -> str:
        return f"nats://127.0.0.1:{self.port}"

    async def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._holding:
            await self._hold(reader, writer)
            return
        upstream_reader, upstream_writer = await asyncio.open_connection(*self._upstream)
        self._writers |= {writer, upstream_writer}
        await asyncio.gather(self._relay(reader, upstream_writer), self._relay(upstream_reader, writer))

    def cut(self) -> None:
        self._drop()
        if self._listening is not None:
            self._listening.close()
            self._listening = None

    def hold(self) -> None:
        self._drop()
        self._holding = True

    def freeze(self) -> None:
        """Keep every connection open and relay nothing more, either way: a server that stopped answering."""
        self._frozen = True

    async def restore(self) -> None:
        """Relay again, listening on the port it had (any free one, the first time)."""
        self._holding = False
        if self._listening is None:
            self._listening = await asyncio.start_server(self.accept, "127.0.0.1", self.port)
            self._listeners.append(self._listening)
            self.port = ADDRESS.validate_python(self._listening.sockets[0].getsockname())[1]

    async def _relay(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError):
            while chunk := await source.read(65536):
                if not self._frozen:
                    sink.write(chunk)
                    await sink.drain()
        sink.close()

    async def holds_nothing(self) -> bool:
        """Whether every connection it held has been closed by its client."""
        return self.held == 0

    def _drop(self) -> None:
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def _hold(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Say nothing until the client closes its end (or the proxy is cut)."""
        self.held += 1
        self._writers.add(writer)
        try:
            with contextlib.suppress(ConnectionError):
                await reader.read()
        finally:
            self.held -= 1
            writer.close()


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


async def created_by_someone_else(api: ConsumerApi, config: ConsumerConfig) -> None:
    """A durable consumer of the test stream, made outside the chassis: nats-py's own ``add_consumer`` leaves
    the rest of its parameters untyped, the protocol does not."""
    await api.add_consumer(STREAM.name, config)


async def readiness(client: httpx.AsyncClient) -> tuple[int, dict[str, bool]]:
    response = await client.get("/readyz")
    return response.status_code, READINESS.validate_json(response.content)["checks"]


async def liveness(client: httpx.AsyncClient) -> tuple[int, list[str]]:
    response = await client.get("/livez")
    if response.status_code == 200:
        return 200, []
    return response.status_code, LIVENESS.validate_json(response.content)["dead"]


async def restarted(registry: CollectorRegistry, worker: str) -> bool:
    return (registry.get_sample_value("ledger_worker_restarts_total", {"worker": worker}) or 0) >= 1


async def nats_is(client: httpx.AsyncClient, *, connected: bool) -> bool:
    return (await readiness(client))[1]["nats"] is connected


async def handled(registry: CollectorRegistry, consumer: str) -> bool:
    """Whether ``consumer`` has handled an event: counted once its transaction committed."""
    return (
        registry.get_sample_value(
            "ledger_consumer_deliveries_total", {"consumer": consumer, "outcome": "handled"}
        )
        == 1
    )


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
        assert await readiness(client) == (
            503,
            {"database": False, "nats": False, "workers": False, "schema": False},
        )
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
        assert await readiness(client) == (
            503,
            {"database": False, "nats": True, "workers": True, "schema": False},
        )


async def test_a_worker_that_something_else_cancelled_is_dead_and_the_service_neither_ready_nor_live(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Nothing restarts a worker that was cancelled: only a restart of the process brings it back."""
    del infrastructure
    payments = Payments(configured(database_url, nats_url))
    async with running(payments.service) as client:
        relay = payments.service.workers[0]
        relay.cancel()
        await asyncio.wait((relay,))
        assert await readiness(client) == (
            503,
            {"database": True, "nats": True, "workers": False, "schema": True},
        )
        assert await liveness(client) == (503, ["workers"])


async def test_a_misconfigured_worker_is_not_restarted_and_the_service_is_neither_ready_nor_live(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """A consumer that can never be brought to its spec (here, someone created it with another ack policy,
    which JetStream never changes) fails the same way however often it starts: the process asks for a
    restart, and a misconfiguration shows as a crash loop."""
    await ensure_stream(infrastructure, STREAM)
    await created_by_someone_else(
        infrastructure,
        ConsumerConfig(
            name=ORPHAN, durable_name=ORPHAN, ack_policy=AckPolicy.NONE, filter_subject="service-test.>"
        ),
    )
    payments = Payments(configured(database_url, nats_url), consumers=(LEDGER, ORPHAN))
    with capture_logs() as logs:
        async with running(payments.service) as client:
            orphan = next(
                worker for worker in payments.service.workers if worker.get_name() == "consumer orphan"
            )
            await asyncio.wait((orphan,), timeout=TIMEOUT)
            assert (await readiness(client))[1] == {
                "database": True,
                "nats": True,
                "workers": False,
                "schema": True,
            }
            assert await liveness(client) == (503, ["workers"])
            registry = payments.service.runtime().registry
            assert (
                registry.get_sample_value("ledger_worker_restarts_total", {"worker": "consumer orphan"}) == 0
            )
    (failure,) = [entry for entry in logs if entry["event"] == "worker.failed"]
    assert isinstance(failure.pop("exc_info"), ConsumerRefusedError)
    assert failure == {"event": "worker.failed", "log_level": "error", "worker": "consumer orphan"}
    assert [entry for entry in logs if entry["event"] == "worker.restarting"] == []


async def test_a_consumer_whose_stream_appears_later_waits_for_it_without_failing_a_probe(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The stream belongs to a service that starts later: a passing absence, not a misconfiguration. The
    consumer says so, is started again after its delay, and binds once the stream is there."""
    payments = Payments(configured(database_url, nats_url), consumers=(LATE,))
    with capture_logs() as logs:
        async with running(payments.service) as client:

            async def missing() -> bool:
                return any(entry["event"] == "consumer.stream_missing" for entry in logs)

            await within(TIMEOUT, missing)
            assert await readiness(client) == (200, ALL_READY)
            assert await liveness(client) == (200, [])

            await ensure_stream(infrastructure, LATER)
            await infrastructure.publish("later.paid", paid().data, headers=paid().headers())
            await within(4 * TIMEOUT, partial(payments.got, LATE, 1))
    (warning, *_) = [entry for entry in logs if entry["event"] == "consumer.stream_missing"]
    assert warning == {
        "event": "consumer.stream_missing",
        "log_level": "warning",
        "consumer": LATE,
        "stream": LATER.name,
    }
    assert [entry for entry in logs if entry["event"] == "worker.failed"] == []


async def test_a_stream_deleted_while_the_service_runs_is_declared_again_and_its_events_still_arrive(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Deleted by mistake, with the consumer bound to it: the relay declares the stream again when an event
    finds none, the consumer's worker binds anew, and the event goes through."""
    payments = Payments(configured(database_url, nats_url))
    with capture_logs() as logs:
        async with running(payments.service) as client:
            await within(TIMEOUT, partial(bound, infrastructure, LEDGER))
            await infrastructure.delete_stream(STREAM.name)
            response = await client.post("/payments")
            await within(6 * TIMEOUT, partial(payments.got, LEDGER, 1))
    assert str(payments.received[LEDGER][0].id) == PAID.validate_json(response.content)["id"]
    assert "outbox.streams_ensured" in [entry["event"] for entry in logs]


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
        service_name="payments",
        database_url=SecretStr(database_url),
        nats_url=SecretStr(nats_url),
        currency="USD",
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

                await proxy.restore()
                await within(4 * TIMEOUT, partial(payments.got, LEDGER, 1))
                assert await readiness(client) == (200, ALL_READY)
    (envelope,) = payments.received[LEDGER]
    assert str(envelope.id) == PAID.validate_json(response.content)["id"]
    events = [entry["event"] for entry in logs]
    assert events.index("nats.disconnected") < events.index("nats.reconnected")
    assert events.count("nats.error") <= 1  # an outage of seconds: the first error, then a minute of silence


@final
class Asked(PermissionSource):
    """Grants reading to anyone, and counts the questions."""

    def __init__(self) -> None:
        self.asked = 0

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        del principal, company
        self.asked += 1
        return frozenset({Permission.COMPANY_READ})


@final
class Watched(PermissionCache):
    """A ``CachedPermissions`` that counts how many times it forgot everything."""

    def __init__(self, cache: CachedPermissions) -> None:
        self.cache = cache
        self.cleared = 0

    @override
    def forget(self, organization: OrganizationId, company: CompanyId, subject: SubjectId) -> None:
        self.cache.forget(organization, company, subject)

    @override
    def forget_company(self, organization: OrganizationId, company: CompanyId) -> None:
        self.cache.forget_company(organization, company)

    @override
    def forget_all(self) -> None:
        self.cleared += 1
        self.cache.forget_all()


async def cleared(watched: Watched, times: int) -> bool:
    return watched.cleared == times


async def test_permissions_kept_before_nats_was_interrupted_are_asked_for_again_after_it(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Membership events published while NATS was unreachable never reach the follower: nothing kept from
    before the interruption is trusted after it."""
    del infrastructure
    source = Asked()
    watched = Watched(CachedPermissions(source))
    alice = Principal(
        subject=SubjectId("2001"), organization=OrganizationId(new_id()), provider_organization="1001"
    )
    company = CompanyId(new_id())
    async with Proxy.to(nats_url) as proxy:
        service = Service(
            settings=ServiceSettings(
                service_name="follower", database_url=SecretStr(database_url), nats_url=SecretStr(proxy.url)
            ),
            version="1.0",
            events=Events(
                workers=lambda runtime: {
                    "permission changes": partial(
                        follow_permission_changes, runtime.nats, watched, runtime.interruptions
                    )
                }
            ),
        )
        async with running(service) as client:
            await within(TIMEOUT, partial(cleared, watched, 1))  # following: nothing kept from before
            for _ in range(2):
                await watched.cache.permissions(alice, company)
            assert source.asked == 1
            proxy.cut()
            await within(TIMEOUT, partial(cleared, watched, 2))  # lost
            await proxy.restore()
            await within(4 * TIMEOUT, partial(cleared, watched, 3))  # back
            assert await readiness(client) == (200, ALL_READY)
            await watched.cache.permissions(alice, company)
            assert source.asked == 2


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
    """Within its time limit, the first connection is retried, each attempt logged, instead of failing."""
    del infrastructure
    async with Proxy.to(nats_url) as proxy:
        proxy.cut()
        payments = Payments(configured(database_url, proxy.url))
        with capture_logs() as logs:

            async def refused() -> bool:
                return any(entry["event"] == "nats.error" for entry in logs)

            async def restore_once_refused() -> None:
                await within(4 * TIMEOUT, refused)
                await proxy.restore()

            restoring = asyncio.create_task(restore_once_refused())
            async with asyncio.timeout(4 * TIMEOUT), running(payments.service) as client:
                assert await readiness(client) == (200, ALL_READY)
            await restoring
    (attempt, *_) = [entry for entry in logs if entry["event"] == "nats.error"]
    assert set(attempt) == {"event", "log_level", "error", "detail"}
    assert attempt["log_level"] == "warning"


@pytest.mark.parametrize("publish_timeout", [5.001, 6.0, 10.0, inf, nan])
def test_a_publish_timeout_beyond_half_the_database_s_idle_limit_is_refused(publish_timeout: float) -> None:
    """The relay waits for JetStream inside its transaction: PostgreSQL ends a session idle for the limit,
    and the pass needs the other half for its statements."""
    database = DatabaseLimits(idle_in_transaction_session_timeout=timedelta(seconds=10))
    with pytest.raises(
        InvalidServiceOptionError,
        match=(
            r"^service option publish_timeout must be at most 5\.0 seconds \(50% of the database's "
            r"idle_in_transaction_session_timeout\), not float: "
        ),
    ):
        ServiceLimits(database=database, publish_timeout=publish_timeout)


def test_a_publish_timeout_of_half_the_database_s_idle_limit_is_accepted() -> None:
    database = DatabaseLimits(idle_in_transaction_session_timeout=timedelta(seconds=10))
    assert ServiceLimits(database=database, publish_timeout=5.0).publish_timeout == 5.0


WAITS: Final[dict[str, Callable[[float], ServiceLimits]]] = {
    "nats_connect_timeout": lambda seconds: ServiceLimits(nats_connect_timeout=seconds),
    "grpc_stop_grace": lambda seconds: ServiceLimits(grpc_stop_grace=seconds),
    "worker_stop_grace": lambda seconds: ServiceLimits(worker_stop_grace=seconds),
    "resources_close_timeout": lambda seconds: ServiceLimits(resources_close_timeout=seconds),
    "nats_drain_timeout": lambda seconds: ServiceLimits(nats_drain_timeout=seconds),
    "span_flush_timeout": lambda seconds: ServiceLimits(span_flush_timeout=seconds),
}


@pytest.mark.parametrize("option", WAITS)
@pytest.mark.parametrize("seconds", [0.0, -1.0, nan, inf])
def test_a_wait_that_is_not_a_positive_number_of_seconds_is_refused(option: str, seconds: float) -> None:
    with pytest.raises(InvalidServiceOptionError, match=rf"^service option {option} must be a positive"):
        WAITS[option](seconds)


@pytest.mark.parametrize("pool_size", [0, -1])
def test_a_pool_without_a_connection_is_refused_with_the_limits(pool_size: int) -> None:
    with pytest.raises(InvalidServiceOptionError, match=r"^service option pool_size must be at least 1"):
        ServiceLimits(pool_size=pool_size)


async def test_the_pool_has_the_size_the_limits_say(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    payments = Payments(configured(database_url, nats_url), limits=ServiceLimits(pool_size=3))
    async with running(payments.service):
        pool = payments.service.runtime().database.engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.size() == 3


def test_the_waits_have_their_defaults() -> None:
    limits = ServiceLimits()
    assert limits.nats_connect_timeout == 60.0
    assert (limits.grpc_stop_grace, limits.worker_stop_grace, limits.nats_drain_timeout) == (8.0, 8.0, 4.0)
    assert limits.span_flush_timeout == 4.0
    assert limits.resources_close_timeout == 1.0


def test_a_stop_takes_no_more_than_twenty_five_seconds_by_default() -> None:
    """Kubernetes kills a pod ``terminationGracePeriodSeconds`` (30 by default) after asking it to stop: the
    chassis's own stop fits with room for the ASGI server's, which comes first."""
    limits = ServiceLimits()
    assert limits.stop_budget == 25.0
    parts = (
        limits.grpc_stop_grace,
        limits.worker_stop_grace,
        limits.resources_close_timeout,
        limits.nats_drain_timeout,
        limits.span_flush_timeout,
    )
    assert limits.stop_budget == sum(parts)


async def test_what_the_outbox_refuses_of_its_publish_timeout_fails_the_start(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The outbox's own rules for the timeout are checked by the outbox alone, when the service starts."""
    del infrastructure
    payments = Payments(configured(database_url, nats_url), limits=ServiceLimits(publish_timeout=0.0))
    with pytest.raises(InvalidOutboxOptionError, match="publish_timeout"):
        async with running(payments.service):
            pass


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


async def test_spans_reach_the_collector_without_exception_messages_paths_or_query_strings(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    collector = Collector()
    async with serve_grpc(port=0, register=collector.register, registry=CollectorRegistry()) as port:
        payments = Payments(configured(database_url, nats_url, otlp_endpoint=f"http://127.0.0.1:{port}"))
        async with running(payments.service) as client:
            failed = await client.get("/settle", params={"rnc": RNC})
            assert failed.status_code == 500
            found = await client.get(f"/taxpayers/{RNC}")
            assert found.status_code == 200
        # stopping the service exported what was pending, to the collector that is still listening
    exported = "".join(str(request) for request in collector.requests)
    assert SECRET not in exported
    assert RNC not in exported
    spans = collector.spans()
    request, settling = spans["GET /settle"], spans["settle"]
    assert texts(request.attributes)["error.type"] == f"{SettlementError.__module__}.SettlementError"
    assert "url.query" not in texts(request.attributes)
    looked_up = spans["GET /taxpayers/{rnc}"]
    assert texts(looked_up.attributes)["http.route"] == "/taxpayers/{rnc}"
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
    stopping = Payments(configured(database_url, nats_url), hold="politely")
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


async def test_a_consumer_that_cannot_reach_jetstream_as_it_starts_is_restarted_once_nats_is_back(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """The connection drops before the consumer has bound: its first request times out, the supervisor
    starts it again after a delay, and it resumes once NATS is back. Meanwhile it is not dead: the service
    stays live, and its readiness says only that NATS is away, never that a worker stopped."""
    del infrastructure
    async with Proxy.to(nats_url) as proxy:
        payments = Payments(configured(database_url, proxy.url), while_starting=proxy.cut)
        with capture_logs() as logs:
            async with running(payments.service) as client:

                async def restarting() -> bool:
                    return any(entry["event"] == "worker.restarting" for entry in logs)

                await within(4 * TIMEOUT, restarting)
                assert await readiness(client) == (
                    503,
                    {"database": True, "nats": False, "workers": True, "schema": True},
                )
                assert await liveness(client) == (200, [])

                await proxy.restore()
                response = await client.post("/payments")
                await within(4 * TIMEOUT, partial(payments.got, LEDGER, 1))
                assert await readiness(client) == (200, ALL_READY)
                assert await restarted(payments.service.runtime().registry, f"consumer {LEDGER}")
    (first, *_) = [entry for entry in logs if entry["event"] == "worker.restarting"]
    assert (first["worker"], first["log_level"]) == (f"consumer {LEDGER}", "warning")
    assert str(payments.received[LEDGER][0].id) == PAID.validate_json(response.content)["id"]


@pytest.mark.parametrize("nats", ["refusing", "silent"])
async def test_a_first_connection_to_nats_that_does_not_come_fails_the_start_and_leaves_nothing_open(
    database_url: str, nats_url: str, nats: str
) -> None:
    """Refused, or accepted and never answered: past its time limit the start fails (the process exits, to be
    started again), and the connection attempt in flight is closed with it."""
    async with Proxy.to(nats_url) as proxy:
        if nats == "refusing":
            proxy.cut()
        else:
            proxy.hold()
        payments = Payments(
            configured(database_url, proxy.url), limits=ServiceLimits(nats_connect_timeout=0.5)
        )
        with pytest.raises(NatsUnreachableError, match=r"^NATS could not be reached within 0\.5 seconds$"):
            async with asyncio.timeout(TIMEOUT), running(payments.service):
                pass
        await within(TIMEOUT, proxy.holds_nothing)


async def test_a_worker_that_will_not_stop_is_left_behind_after_its_grace_and_the_stop_goes_on(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    stopping = Payments(
        configured(database_url, nats_url),
        hold="stubbornly",
        limits=ServiceLimits(worker_stop_grace=WINDING_DOWN),
    )
    with capture_logs() as logs:
        async with asyncio.timeout(TIMEOUT), running(stopping.service) as client:
            await client.post("/payments")
            await stopping.handling.wait()
            connection = stopping.service.runtime().nats
            consumer = stopping.service.workers[1]
    assert connection.is_closed  # the stop went on past the consumer, down to NATS
    assert not consumer.done()
    not_stopped = [entry for entry in logs if entry["event"] == "worker.not_stopped"]
    assert not_stopped == [
        {
            "event": "worker.not_stopped",
            "log_level": "error",
            "worker": f"consumer {LEDGER}",
            "grace": WINDING_DOWN,
        }
    ]
    consumer.cancel()  # asked again, it stops
    await asyncio.wait((consumer,))


async def test_routes_are_handed_an_asynchronous_dependency_that_gives_them_the_runtime_while_it_runs(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Asynchronous, FastAPI resolves it on the event loop, without a trip through its thread pool."""
    del infrastructure
    handed: list[RuntimeDependency[ServiceSettings]] = []

    def routes(runtime: RuntimeDependency[ServiceSettings]) -> Sequence[APIRouter]:
        handed.append(runtime)
        return ()

    service = Service(
        settings=configured(database_url, nats_url), version="1.0", interfaces=Interfaces(http=routes)
    )
    (runtime,) = handed
    assert inspect.iscoroutinefunction(runtime)
    with pytest.raises(ServiceNotRunningError):
        await runtime()
    async with running(service):
        assert await runtime() is service.runtime()


async def test_the_credentials_in_the_nats_url_never_reach_a_log(database_url: str, nats_url: str) -> None:
    """Each failed attempt to connect is logged, and the start fails with an error: neither says the URL."""
    async with Proxy.to(nats_url) as proxy:
        proxy.cut()
        with_credentials = proxy.url.replace("nats://", "nats://app:s3cret@")
        payments = Payments(
            configured(database_url, with_credentials), limits=ServiceLimits(nats_connect_timeout=TIMEOUT / 2)
        )
        with capture_logs() as logs, pytest.raises(NatsUnreachableError) as unreachable:
            async with asyncio.timeout(TIMEOUT), running(payments.service):
                pass
    assert [entry["event"] for entry in logs if entry["event"] == "nats.error"] != []
    assert "s3cret" not in "".join(repr(entry) for entry in logs)
    assert "s3cret" not in str(unreachable.value)


async def test_a_nats_that_stopped_answering_is_drained_no_longer_than_its_limit(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Connected, as far as the client can tell, and silent: nats-py would wait for its flush (ten seconds)
    and its drain (thirty) before it closed. The stop closes it once its limit is over."""
    async with Proxy.to(nats_url) as proxy:
        payments = Payments(configured(database_url, proxy.url), limits=ServiceLimits(nats_drain_timeout=0.3))
        with capture_logs() as logs:
            async with running(payments.service):
                connection = payments.service.runtime().nats
                await within(TIMEOUT, partial(bound, infrastructure, LEDGER))
                proxy.freeze()
                stopping = monotonic()
            stopped_in = monotonic() - stopping
    assert connection.is_closed
    assert stopped_in < 3
    assert {"event": "nats.drain_timed_out", "log_level": "warning", "timeout": 0.3} in logs


async def test_a_stop_in_which_every_part_hangs_ends_within_the_sum_of_its_limits(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """A gRPC call that never ends, a handler that ignores its cancellation, a NATS that stopped answering, a
    collector that never answers: each part is given its limit, and the stop goes on."""
    del infrastructure
    limit = 0.3
    limits = ServiceLimits(
        grpc_stop_grace=limit, worker_stop_grace=limit, nats_drain_timeout=limit, span_flush_timeout=limit
    )
    async with Proxy.to(nats_url) as nats, Proxy.to(nats_url) as collector:
        collector.hold()  # accepts, and never answers
        stopping = Payments(
            configured(database_url, nats.url, otlp_endpoint=f"http://127.0.0.1:{collector.port}"),
            hold="stubbornly",
            limits=limits,
        )
        async with running(stopping.service) as client:
            await client.post("/payments")
            async with asyncio.timeout(TIMEOUT):
                await stopping.handling.wait()
            in_flight = asyncio.create_task(call(running_port(stopping.service), "Slow"))
            await stopping.slow_started.wait()
            consumer = stopping.service.workers[1]
            nats.freeze()
            started = monotonic()
        stopped_in = monotonic() - started
        consumer.cancel()  # asked again, it stops
        await asyncio.wait((consumer,))
        with pytest.raises(grpc.aio.AioRpcError):
            await in_flight
    assert stopped_in < limits.stop_budget + 2  # the pool and the rest take milliseconds


# ── Extension points: workers, checks, resources ────────────────────────────────


@final
@dataclass(frozen=True, slots=True)
class Clients:
    """What a service opens beside the chassis: here, one stand-in for an HTTP client."""

    dgii: str


@final
class Extended:
    """A service with a worker of its own, a readiness check of its own and resources of its own."""

    def __init__(self, settings: ServiceSettings, *, healthy: bool = True, fail_once: bool = False) -> None:
        self.happened: list[str] = []
        self.ticks = 0
        self.checked: list[Runtime[ServiceSettings]] = []
        self.healthy = healthy
        self._fail_once = fail_once
        self.service = Service(
            settings=settings,
            version="1.0",
            interfaces=Interfaces(
                http=self._routes, resources=self._open, checks={"dgii": self._dgii_answers}
            ),
            events=Events(workers=self._workers),
        )

    @asynccontextmanager
    async def _open(self, settings: ServiceSettings) -> AsyncGenerator[Clients]:
        self.happened.append("resources opened")
        try:
            yield Clients(dgii=f"client of {settings.service_name}")
        finally:
            self.happened.append("resources closed")

    async def _dgii_answers(self, runtime: Runtime[ServiceSettings]) -> None:
        self.checked.append(runtime)
        if not self.healthy:
            raise ConnectionError

    def _workers(
        self, runtime: Runtime[ServiceSettings]
    ) -> dict[str, Callable[[], Coroutine[object, object, Never]]]:
        del runtime
        return {"ticker": self._tick}

    async def _tick(self) -> Never:
        self.ticks += 1
        if self._fail_once and self.ticks == 1:
            raise TickFailedError
        try:
            await asyncio.Event().wait()
        finally:
            self.happened.append("worker stopped")
        raise AssertionError  # never reached: the wait above ends only by cancellation

    def _routes(self, runtime: RuntimeDependency[ServiceSettings]) -> Sequence[APIRouter]:
        router = APIRouter()

        @router.get("/dgii")
        async def dgii(current: Annotated[Runtime[ServiceSettings], Depends(runtime)]) -> str:
            return current.resources(Clients).dgii

        return (router,)


class TickFailedError(Exception):
    def __init__(self) -> None:
        super().__init__("the first tick fails")


async def test_a_service_s_own_worker_runs_supervised_and_stops_before_its_resources_close(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    extended = Extended(configured(database_url, nats_url), fail_once=True)
    async with running(extended.service):
        registry = extended.service.runtime().registry
        await within(4 * TIMEOUT, partial(restarted, registry, "ticker"))
        assert [worker.get_name() for worker in extended.service.workers] == ["outbox relay", "ticker"]
    assert extended.ticks == 2
    assert extended.happened == ["resources opened", "worker stopped", "resources closed"]


async def test_routes_reach_the_resources_typed_and_a_wrong_type_is_refused(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    extended = Extended(configured(database_url, nats_url))
    async with running(extended.service) as client:
        response = await client.get("/dgii")
        assert response.json() == "client of payments"
        with pytest.raises(UnexpectedResourcesError, match=r"^the service's resources are Clients, not int$"):
            extended.service.runtime().resources(int)


async def test_a_service_without_resources_has_none_to_hand(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    service = bare(database_url, nats_url)
    async with running(service):
        with pytest.raises(
            UnexpectedResourcesError, match=r"^the service's resources are NoneType, not Clients$"
        ):
            service.runtime().resources(Clients)


@pytest.mark.parametrize("healthy", [True, False])
async def test_a_service_s_own_check_is_part_of_its_readiness_and_is_handed_the_runtime(
    database_url: str, nats_url: str, infrastructure: JetStreamContext, *, healthy: bool
) -> None:
    del infrastructure
    extended = Extended(configured(database_url, nats_url), healthy=healthy)
    transport = httpx.ASGITransport(app=extended.service.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as before:
        assert (await readiness(before))[1]["dgii"] is False  # not running: nothing to check with
    async with running(extended.service) as client:
        status, checks = await readiness(client)
        assert (status == 200, checks) == (healthy, ALL_READY | {"dgii": healthy})
        assert extended.checked == [extended.service.runtime()]


def test_a_check_of_the_service_cannot_take_the_name_of_one_of_the_chassis(database_url: str) -> None:
    with pytest.raises(DuplicateCheckError, match="'database'"):
        Service(
            settings=configured(database_url, "nats://localhost:4222"),
            version="1.0",
            interfaces=Interfaces(checks={"database": partial(asyncio.sleep, 0)}),
        )


async def test_a_worker_cannot_take_the_name_of_one_of_the_chassis(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure

    async def forever() -> Never:
        await asyncio.Event().wait()
        raise AssertionError

    service = Service(
        settings=configured(database_url, nats_url),
        version="1.0",
        events=Events(workers=lambda _: {"outbox relay": forever}),
    )
    with pytest.raises(DuplicateWorkerError, match=r"^a worker named 'outbox relay' already runs$"):
        async with running(service):
            pass


async def test_a_database_without_the_chassis_tables_is_not_ready(
    database_url: str, nats_url: str, jetstream: JetStreamContext
) -> None:
    """Migrations not run: the relay would fail at every pass while the service answered ready."""
    del jetstream
    service = bare(database_url, nats_url)
    async with running(service) as client:
        assert await readiness(client) == (503, ALL_READY | {"schema": False})


def counting_schema_checks(monkeypatch: pytest.MonkeyPatch) -> list[Sequence[str]]:
    """The real ``missing_tables``, noting each time the readiness check asks it (a connection each time)."""
    asked: list[Sequence[str]] = []
    real = Database.missing_tables

    async def spy(self: Database, names: Sequence[str]) -> tuple[str, ...]:
        asked.append(names)
        return await real(self, names)

    monkeypatch.setattr(Database, "missing_tables", spy)
    return asked


async def test_once_the_schema_is_there_a_probe_no_longer_opens_a_connection_to_look(
    database_url: str, nats_url: str, infrastructure: JetStreamContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    del infrastructure
    asked = counting_schema_checks(monkeypatch)
    service = bare(database_url, nats_url)
    async with running(service) as client:
        for _ in range(3):
            assert await readiness(client) == (200, ALL_READY)
    assert len(asked) == 1


async def test_a_schema_that_is_missing_is_looked_for_at_every_probe(
    database_url: str, nats_url: str, jetstream: JetStreamContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    del jetstream
    asked = counting_schema_checks(monkeypatch)
    service = bare(database_url, nats_url)
    async with running(service) as client:
        for _ in range(3):
            assert await readiness(client) == (503, ALL_READY | {"schema": False})
    assert len(asked) == 3


async def test_the_metrics_of_the_process_are_exported(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    service = bare(database_url, nats_url)
    async with running(service) as client:
        metrics = (await client.get("/metrics")).text
    assert "\npython_info{" in metrics
    assert "\npython_gc_objects_collected_total{" in metrics
    if sys.platform == "linux":  # the process collector reads /proc
        assert "\nprocess_resident_memory_bytes " in metrics


async def test_metrics_made_before_the_service_are_exported_with_its_own_through_the_registry_it_is_given(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    registry = CollectorRegistry()
    Gauge("ledger_test_made_before", "A metric made before the service.", registry=registry).set(7)
    settings = ServiceSettings(
        service_name="given", database_url=SecretStr(database_url), nats_url=SecretStr(nats_url)
    )
    service = Service(settings=settings, version="1.0", interfaces=Interfaces(registry=registry))
    async with running(service) as client:
        metrics = (await client.get("/metrics")).text
        assert service.runtime().registry is registry
    assert "\nledger_test_made_before 7.0\n" in metrics
    assert "\npython_info{" in metrics


# ── Resources that will not close ───────────────────────────────────────────────


@final
class Unclosable:
    """Resources whose close never ends, and ignores its cancellation until ``release``."""

    def __init__(self, *, fails_in_the_end: bool = False) -> None:
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self._fails_in_the_end = fails_in_the_end

    @asynccontextmanager
    async def open(self, settings: ServiceSettings) -> AsyncGenerator[Clients]:
        try:
            yield Clients(dgii=settings.service_name)
        finally:
            self.closing.set()
            while not self.release.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await self.release.wait()
            if self._fails_in_the_end:
                raise CloseFailedError


class CloseFailedError(Exception):
    def __init__(self) -> None:
        super().__init__("the close failed")


async def test_a_client_that_is_closed_by_another_task_than_the_one_that_opened_it_closes_correctly(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    """Resources are closed by the stop, a task other than the start's: an HTTP client does not mind."""
    clients: list[httpx.AsyncClient] = []

    @asynccontextmanager
    async def opener(settings: ServiceSettings) -> AsyncGenerator[httpx.AsyncClient]:
        del settings
        async with httpx.AsyncClient() as client:
            clients.append(client)
            yield client

    del infrastructure
    service = Service(
        settings=configured(database_url, nats_url), version="1.0", interfaces=Interfaces(resources=opener)
    )
    async with running(service):
        assert not clients[0].is_closed
    assert clients[0].is_closed


async def test_resources_that_will_not_close_are_abandoned_after_their_limit_and_the_stop_goes_on(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    resources = Unclosable()
    service = Service(
        settings=configured(database_url, nats_url),
        version="1.0",
        interfaces=Interfaces(resources=resources.open),
        limits=ServiceLimits(resources_close_timeout=WINDING_DOWN),
    )
    with capture_logs() as logs:
        async with asyncio.timeout(TIMEOUT), running(service):
            connection = service.runtime().nats
            stopping = monotonic()
        stopped_in = monotonic() - stopping
    assert len(ABANDONED_CLOSES) == 1  # held by the module while it runs: nothing else refers to it
    resources.release.set()
    await within(TIMEOUT, partial(emptied, ABANDONED_CLOSES))
    assert resources.closing.is_set()
    assert connection.is_closed  # the stop went on past the resources, down to NATS
    assert stopped_in < 2  # the limit of the resources held, not the second they are given by default
    assert {"event": "resources.close_abandoned", "log_level": "error", "timeout": WINDING_DOWN} in logs


async def test_resources_abandoned_at_the_stop_that_fail_later_are_logged_and_not_lost(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    resources = Unclosable(fails_in_the_end=True)
    service = Service(
        settings=configured(database_url, nats_url),
        version="1.0",
        interfaces=Interfaces(resources=resources.open),
        limits=ServiceLimits(resources_close_timeout=WINDING_DOWN),
    )
    with capture_logs() as logs:
        async with asyncio.timeout(TIMEOUT), running(service):
            pass
        resources.release.set()
        await within(TIMEOUT, partial(logged, logs, "resources.close_failed_after_abandon"))
    (entry,) = [entry for entry in logs if entry["event"] == "resources.close_failed_after_abandon"]
    assert isinstance(entry["exc_info"], CloseFailedError)


async def test_resources_whose_close_fails_within_the_limit_fail_the_stop_as_before(
    database_url: str, nats_url: str, infrastructure: JetStreamContext
) -> None:
    del infrastructure
    resources = Unclosable(fails_in_the_end=True)
    resources.release.set()
    service = Service(
        settings=configured(database_url, nats_url),
        version="1.0",
        interfaces=Interfaces(resources=resources.open),
    )
    connections: list[Client] = []
    with pytest.raises(CloseFailedError):
        async with asyncio.timeout(TIMEOUT), running(service):
            connections.append(service.runtime().nats)
    assert connections[0].is_closed  # the failure did not keep the rest of the stop from running


async def emptied(held: AbstractSet[object]) -> bool:
    return not held


async def logged(logs: Sequence[MutableMapping[str, object]], event: str) -> bool:
    return any(entry["event"] == event for entry in logs)
