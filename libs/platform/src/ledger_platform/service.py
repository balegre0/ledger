"""A ledger service: database, events, tracing, health, HTTP and gRPC, started and stopped as one.

``Service`` builds the FastAPI app; the app's lifespan starts the rest, in this order: the database pool, the
connection to NATS, ``DEAD_LETTERS`` and the service's own streams, the outbox relay, one worker per consumer,
the gRPC server. Stopping goes the other way and waits for each part: the gRPC server stops taking calls and
lets those in flight finish, the workers are cancelled and awaited (newest first), the connection to NATS
drains, the pool closes, and the spans still pending are exported. A start that fails halfway releases what
had started, in the same order.

``/readyz`` checks that PostgreSQL answers, that NATS is connected and that no worker has stopped. A worker
that stops on its own (a consumer whose stream is gone) is logged once with its error, and the service stays
not ready from then on.

NATS is reconnected for as long as it takes, at the first connection too: the outbox keeps the events
meanwhile and the consumers wait. Each failed attempt, disconnection and reconnection is logged, never what
was being sent.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import ClassVar, Final, Never, final
lazy from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence

import structlog
from nats.aio.client import Client
from nats.js import JetStreamContext
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import CollectorRegistry
lazy import grpc
lazy from fastapi import APIRouter, FastAPI
lazy from opentelemetry.sdk.trace.export import SpanExporter

from ledger_kernel.errors import LedgerError, UnavailableError, truncate
from ledger_platform.consumer import Consumer, ConsumerMetrics
from ledger_platform.database import DEFAULT_LIMITS, Database
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.health import Health
from ledger_platform.lifecycle import Lifecycle
from ledger_platform.logs import configure_logging
from ledger_platform.options import InvalidOptionError, require_seconds
from ledger_platform.outbox import DEFAULT_PUBLISH_TIMEOUT, MAX_PUBLISH_TIMEOUT, Outbox
from ledger_platform.streams import DEAD_LETTERS, ensure_stream
from ledger_platform.tracing import Tracing
from ledger_platform.web import ApiInfo, Observability, create_app
lazy from ledger_platform.consumer import ConsumerSpec
lazy from ledger_platform.database import DatabaseLimits
lazy from ledger_platform.settings import ServiceSettings
lazy from ledger_platform.streams import StreamSpec

_RECONNECT_WAIT: Final = 2
"""Seconds between two attempts to reach NATS: nats-py's own default, short enough for a blip."""
_UNLIMITED: Final = -1
"""nats-py's word for "never stop reconnecting"."""
_DETAIL_LIMIT: Final = 200
"""What a log line keeps of the message of a NATS error."""
_logger = structlog.stdlib.get_logger(__name__)


class ServiceNotRunningError(UnavailableError):
    """The service is starting, stopping or stopped: what it runs on is not there."""

    code: ClassVar[str] = "service_not_running"

    def __init__(self) -> None:
        super().__init__("the service is not running")


class ServiceAlreadyStartedError(LedgerError):
    """A service was started a second time. A bug in the code that runs it.

    Its metrics and its tracer provider belong to its one run: the metrics would be registered twice, and the
    provider is shut down when the service stops.
    """

    code: ClassVar[str] = "service_already_started"

    def __init__(self) -> None:
        super().__init__("a service starts once: build a new one to start again")


class InvalidServiceOptionError(InvalidOptionError):
    """A service option that cannot work."""

    code: ClassVar[str] = "invalid_service_option"
    label: ClassVar[str] = "service option"


class _NatsDisconnectedError(UnavailableError):
    """NATS is not connected: the client is reconnecting, or the connection was closed."""

    code: ClassVar[str] = "nats_disconnected"

    def __init__(self) -> None:
        super().__init__("not connected to NATS")


class _WorkerStoppedError(UnavailableError):
    """A background worker is no longer running."""

    code: ClassVar[str] = "worker_stopped"

    def __init__(self, workers: Sequence[str]) -> None:
        super().__init__(f"background workers stopped: {', '.join(workers)}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Runtime[S: ServiceSettings]:
    """What a running service's routes, handlers and gRPC methods work with."""

    settings: S
    database: Database
    nats: Client
    jetstream: JetStreamContext
    outbox: Outbox
    """Where routes and handlers store the events they announce, in their own transaction."""
    tracing: Tracing
    registry: CollectorRegistry
    """The registry ``/metrics`` exports, for metrics of the service's own."""


type ConsumerFactory[S: ServiceSettings] = Callable[[Runtime[S]], Sequence[ConsumerSpec]]
"""The consumers of a service, made once it runs, so that their handlers can use what it runs on."""
type GrpcServices[S: ServiceSettings] = Callable[[Runtime[S], grpc.aio.Server], None]
"""Adds a service's gRPC services to its server, once it runs."""


def _no_consumers[S: ServiceSettings](runtime: Runtime[S]) -> Sequence[ConsumerSpec]:
    del runtime
    return ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Interfaces[S: ServiceSettings]:
    """How clients reach a service: its HTTP routes and its gRPC services."""

    http: Sequence[APIRouter] = ()
    """Complete before the service is built: a route added later is measured as ``unregistered``."""
    grpc: GrpcServices[S] | None = None
    """``None``: the service runs no gRPC server."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Events[S: ServiceSettings]:
    """What a service publishes to and what it consumes."""

    streams: Sequence[StreamSpec] = ()
    """The streams of the events it publishes, created or updated at start (``DEAD_LETTERS`` always is)."""
    consumers: ConsumerFactory[S] = _no_consumers


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceLimits:
    """How long a service waits on PostgreSQL and JetStream, and PostgreSQL on the service.

    The relay waits for JetStream's acknowledgements of a batch inside the transaction that locks its rows,
    so ``publish_timeout`` must be below ``database.idle_in_transaction_session_timeout``: PostgreSQL ends a
    session idle in its transaction for that long, and every pass that waited would fail.
    """

    database: DatabaseLimits = DEFAULT_LIMITS
    publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT
    """Seconds the relay waits for the acknowledgements of a batch (at most ``MAX_PUBLISH_TIMEOUT``)."""

    def __post_init__(self) -> None:
        require_seconds(
            InvalidServiceOptionError, "publish_timeout", self.publish_timeout, ceiling=MAX_PUBLISH_TIMEOUT
        )
        idle = self.database.idle_in_transaction_session_timeout.total_seconds()
        if self.publish_timeout >= idle:
            raise InvalidServiceOptionError(
                option="publish_timeout",
                requirement=f"below the database's idle_in_transaction_session_timeout ({idle} seconds)",
                value=self.publish_timeout,
            )


_DEFAULT_LIMITS: Final = ServiceLimits()


def _exporter(settings: ServiceSettings) -> SpanExporter | None:
    """An OTLP/gRPC exporter to the collector the settings name; none when they name none."""
    endpoint = settings.otlp_endpoint
    return None if endpoint is None else OTLPSpanExporter(endpoint=endpoint)


@final
class _ConnectionLog:
    """What happens to a connection to NATS, in the service's log: what failed, never what was being sent."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    async def failed(self, error: Exception) -> None:
        _logger.warning("nats.error", error=type(error).__name__, detail=truncate(str(error), _DETAIL_LIMIT))

    async def disconnected(self) -> None:
        if not self._client.is_closed:  # closing it on purpose says nothing worth a warning
            _logger.warning("nats.disconnected")

    async def reconnected(self) -> None:
        _logger.info("nats.reconnected")


async def _connect(settings: ServiceSettings) -> Client:
    """A connection to NATS that keeps reconnecting, for as long as it takes, until it is closed."""
    client = Client()
    log = _ConnectionLog(client)
    await client.connect(
        servers=settings.nats_url,
        name=settings.service_name,
        max_reconnect_attempts=_UNLIMITED,
        reconnect_time_wait=_RECONNECT_WAIT,
        error_cb=log.failed,
        disconnected_cb=log.disconnected,
        reconnected_cb=log.reconnected,
    )
    return client


async def _disconnect(client: Client) -> None:
    """Drain a live connection: what it has buffered goes out and its subscriptions end. Close any other.

    Draining refuses a connection that is closed (someone closed it) or reconnecting (NATS is unreachable):
    closing stops the reconnecting, and leaves a closed one as it is.
    """
    try:
        if client.is_connected:
            await client.drain()
    finally:
        await client.close()


async def _cancel(worker: asyncio.Task[Never]) -> None:
    """Cancel ``worker`` and wait until it has stopped, whatever it ends with."""
    worker.cancel()
    await asyncio.wait((worker,))


def _report_stopped(worker: asyncio.Task[Never]) -> None:
    """A worker runs until it is cancelled: one that ends otherwise failed, and is logged once, with why."""
    if not worker.cancelled():
        _logger.error("service.worker_failed", worker=worker.get_name(), exc_info=worker.exception())


@final
class Service[S: ServiceSettings]:
    """One service process: the FastAPI app whose lifespan starts and stops everything else (see the module).

    Logging is configured, and the tracer provider made, when the service is built. A service starts once.
    """

    __slots__ = (
        "_app",
        "_events",
        "_grpc_port",
        "_interfaces",
        "_limits",
        "_registry",
        "_runtime",
        "_settings",
        "_started",
        "_tracing",
        "_workers",
    )

    def __init__(
        self,
        *,
        settings: S,
        version: str,
        interfaces: Interfaces[S] | None = None,
        events: Events[S] | None = None,
        limits: ServiceLimits = _DEFAULT_LIMITS,
    ) -> None:
        configure_logging(settings)
        self._settings = settings
        self._interfaces: Interfaces[S] = Interfaces() if interfaces is None else interfaces
        self._events: Events[S] = Events() if events is None else events
        self._limits = limits
        self._registry = CollectorRegistry()
        self._tracing = Tracing.create(service_name=settings.service_name, exporter=_exporter(settings))
        self._runtime: Runtime[S] | None = None
        self._workers: list[asyncio.Task[Never]] = []
        self._grpc_port: int | None = None
        self._started = False
        health = Health()
        health.add("database", self._database_answers)
        health.add("nats", self._nats_is_connected)
        health.add("workers", self._workers_run)
        self._app = create_app(
            info=ApiInfo(title=settings.service_name, version=version),
            observability=Observability(
                health=health, registry=self._registry, tracer_provider=self._tracing.provider
            ),
            lifespan=self._lifespan,
            routers=self._interfaces.http,
        )

    @property
    def app(self) -> FastAPI:
        """The ASGI application to serve: its lifespan runs the service."""
        return self._app

    def runtime(self) -> Runtime[S]:
        """What the running service works with; a FastAPI dependency for routes.

        Raises ``ServiceNotRunningError`` (503 for an HTTP client) while the service is not running.
        """
        if self._runtime is None:
            raise ServiceNotRunningError
        return self._runtime

    @property
    def workers(self) -> tuple[asyncio.Task[Never], ...]:
        """The background workers in the order they started: the outbox relay, then one per consumer."""
        return tuple(self._workers)

    @property
    def grpc_port(self) -> int | None:
        """The port the gRPC server listens on while the service runs; ``None`` otherwise, or without one."""
        return self._grpc_port

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncGenerator[None]:
        del app
        if self._started:
            raise ServiceAlreadyStartedError
        self._started = True
        async with Lifecycle() as lifecycle:
            try:
                self._runtime = await self._start(lifecycle)
                yield
            finally:
                self._runtime = None  # stopping: not ready, and routes answer 503
                self._grpc_port = None

    async def _start(self, lifecycle: Lifecycle) -> Runtime[S]:
        """Start every part, each released by ``lifecycle`` in reverse order; the runtime they make up."""
        settings, limits, tracing = self._settings, self._limits, self._tracing
        lifecycle.on_close(self._export_pending_spans)  # the last to run: spans end until everything stopped
        tracing.register_globally()
        database = await lifecycle.enter(
            Database.open(
                settings.database_url.get_secret_value(),
                application_name=settings.service_name,
                limits=limits.database,
            )
        )
        connection = await _connect(settings)
        lifecycle.on_close(partial(_disconnect, connection))
        jetstream = JetStreamContext(connection)
        for stream in (DEAD_LETTERS, *self._events.streams):
            await ensure_stream(jetstream, stream)
        outbox = Outbox(
            tracing=tracing,
            registry=self._registry,
            publish_timeout=limits.publish_timeout,
            max_payload=connection.max_payload,
        )
        runtime = Runtime(
            settings=settings,
            database=database,
            nats=connection,
            jetstream=jetstream,
            outbox=outbox,
            tracing=tracing,
            registry=self._registry,
        )
        self._spawn(lifecycle, "outbox relay", outbox.run(database, jetstream))
        metrics = ConsumerMetrics(self._registry)
        for spec in self._events.consumers(runtime):
            consumer = Consumer(spec, database=database, tracing=tracing, metrics=metrics)
            self._spawn(lifecycle, f"consumer {spec.name}", consumer.run(jetstream))
        services = self._interfaces.grpc
        if services is not None:
            self._grpc_port = await lifecycle.enter(
                serve_grpc(port=settings.grpc_port, register=partial(services, runtime))
            )
        return runtime

    def _spawn(self, lifecycle: Lifecycle, name: str, work: Coroutine[object, object, Never]) -> None:
        """Run ``work`` as a worker named ``name``, cancelled and awaited when ``lifecycle`` closes."""
        worker = asyncio.create_task(work, name=name)
        worker.add_done_callback(_report_stopped)
        self._workers.append(worker)
        lifecycle.on_close(partial(_cancel, worker))

    async def _export_pending_spans(self) -> None:
        await asyncio.to_thread(self._tracing.shutdown)

    async def _database_answers(self) -> None:
        await self.runtime().database.ping()

    async def _nats_is_connected(self) -> None:
        if not self.runtime().nats.is_connected:
            raise _NatsDisconnectedError

    async def _workers_run(self) -> None:
        self.runtime()  # raises while the service is not running
        stopped = [worker.get_name() for worker in self._workers if worker.done()]
        if stopped:
            raise _WorkerStoppedError(stopped)
