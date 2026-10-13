"""A ledger service: database, events, tracing, health, HTTP and gRPC, started and stopped as one.

``Service`` builds the FastAPI app; the app's lifespan starts the rest, in this order: the database pool, the
connection to NATS, ``DEAD_LETTERS`` and the service's own streams, the outbox relay, one worker per consumer,
the gRPC server. Stopping goes the other way and waits for each part: the gRPC server stops taking calls and
lets those in flight finish, the workers are cancelled and awaited (all together, within one stop grace),
the connection to NATS drains, the pool closes, and the spans still pending are exported. A start that fails
halfway releases what had started, in the same order.

Workers are supervised (``ledger_platform.supervision``): one that fails is started again after a growing
delay, and one that fails on a configuration mistake (``ConsumerRefusedError``, any ``InvalidOptionError``)
is not, since it would fail the same way every time. ``/readyz`` checks that PostgreSQL answers, that NATS is
connected and that no worker is dead. A worker waiting to start again is not dead, so the failure of a worker
alone (JetStream unable to serve its consumer for a moment) never makes the service unready; NATS itself
being unreachable does, through its own check. A dead worker also fails ``/livez``: nothing in the process
brings it back, so the orchestrator restarts the process, and a misconfiguration shows as a crash loop.

The first connection to NATS is retried for ``ServiceLimits.nats_connect_timeout`` seconds, then the
start fails (and the process exits). Once connected, NATS is reconnected for as long as it takes: the outbox
keeps the events meanwhile and the consumers wait. A disconnection and a reconnection are logged, and so are
the errors of the attempts in between: the first at once, then one a minute (``NATS_ERRORS_LOGGED_EVERY``)
saying how many were not, never what was being sent. Both are also told to ``Runtime.interruptions``, for code
that keeps state fresh from core NATS messages, which miss what was published meanwhile.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar, Final, Never, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
from nats.aio.client import Client
from nats.js import JetStreamContext
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
lazy import grpc
lazy from fastapi import APIRouter, FastAPI
lazy from opentelemetry.sdk.trace.export import SpanExporter

from ledger_kernel.errors import LedgerError, UnavailableError, error_type, truncate
from ledger_platform.consumer import Consumer, ConsumerMetrics, ConsumerRefusedError
from ledger_platform.database import DEFAULT_LIMITS, DEFAULT_POOL_SIZE, Database
from ledger_platform.grpc_server import serve_grpc
from ledger_platform.health import Health
from ledger_platform.interruptions import Interruptions
from ledger_platform.lifecycle import Lifecycle
from ledger_platform.logs import Throttled, configure_logging
from ledger_platform.options import InvalidOptionError, require_at_least, require_seconds
from ledger_platform.outbox import DEFAULT_PUBLISH_TIMEOUT, Outbox
from ledger_platform.schema import CHASSIS_METADATA
from ledger_platform.streams import DEAD_LETTERS, ensure_stream
from ledger_platform.supervision import Supervisor
from ledger_platform.tracing import DEFAULT_SHUTDOWN_TIMEOUT, Tracing
from ledger_platform.web import ApiInfo, Observability, create_app
lazy from ledger_platform.consumer import ConsumerSpec
lazy from ledger_platform.database import DatabaseLimits
lazy from ledger_platform.settings import ServiceSettings
lazy from ledger_platform.streams import StreamSpec
lazy from ledger_platform.supervision import Work

_RECONNECT_WAIT: Final = 2
"""Seconds between two attempts to reach NATS: nats-py's own default, short enough for a blip."""
_UNLIMITED: Final = -1
"""nats-py's word for "never stop reconnecting"."""
_DETAIL_LIMIT: Final = 200
"""What a log line keeps of the message of a NATS error."""
_PERMANENT: Final = (ConsumerRefusedError, InvalidOptionError)
"""Failures of a worker that no restart can fix: a consumer JetStream refuses, an option that cannot work."""
_PUBLISH_SHARE_OF_IDLE: Final = 0.5
"""The most of PostgreSQL's ``idle_in_transaction_session_timeout`` a relay pass may spend waiting for
JetStream's acknowledgements: the rest is the margin for the statements of the pass."""
DEFAULT_NATS_CONNECT_TIMEOUT: Final = 60.0
"""Seconds the first connection to NATS is retried before the start fails: long enough for NATS to start
alongside the service, short enough that a wrong address shows as a crash loop."""
DEFAULT_GRPC_STOP_GRACE: Final = 8.0
"""Seconds the calls in flight are given to finish when the service stops."""
DEFAULT_WORKER_STOP_GRACE: Final = 8.0
"""Seconds the workers are given to stop, all together, like the gRPC server's grace: a handler may be
finishing a statement."""
DEFAULT_RESOURCES_CLOSE_TIMEOUT: Final = 1.0
"""Seconds the service's own resources are given to close: an HTTP client or a gRPC channel closes in
milliseconds, so one that takes longer is hung."""
DEFAULT_NATS_DRAIN_TIMEOUT: Final = 4.0
"""Seconds the connection to NATS is given to drain (what it buffered goes out) before it is closed."""
NATS_ERRORS_LOGGED_EVERY: Final = 60.0
"""Seconds between two log lines of the errors of a connection to NATS that stays unreachable."""
ABANDONED_CLOSES: Final[set[asyncio.Task[None]]] = set()
"""Closes of resources that ran past their limit and are still running. The event loop holds a task weakly:
a reference kept here stops it from being collected, and cancelled, in the middle of the close."""
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


class NatsUnreachableError(UnavailableError):
    """The first connection to NATS did not come within ``nats_connect_timeout``: the service cannot start."""

    code: ClassVar[str] = "nats_unreachable"

    def __init__(self, seconds: float) -> None:
        super().__init__(f"NATS could not be reached within {seconds} seconds")


class InvalidServiceOptionError(InvalidOptionError):
    """A service option that cannot work."""

    code: ClassVar[str] = "invalid_service_option"
    label: ClassVar[str] = "service option"


class _NatsDisconnectedError(UnavailableError):
    """NATS is not connected: the client is reconnecting, or the connection was closed."""

    code: ClassVar[str] = "nats_disconnected"

    def __init__(self) -> None:
        super().__init__("not connected to NATS")


class _ChassisTablesMissingError(UnavailableError):
    """The chassis's tables are not in the database: its first migration has not run."""

    code: ClassVar[str] = "chassis_tables_missing"

    def __init__(self, tables: Sequence[str]) -> None:
        super().__init__(f"the database lacks the chassis's tables: {', '.join(tables)}")


class _WorkerStoppedError(UnavailableError):
    """A background worker is no longer running."""

    code: ClassVar[str] = "worker_stopped"

    def __init__(self, workers: Sequence[str]) -> None:
        super().__init__(f"background workers stopped: {', '.join(workers)}")


class UnexpectedResourcesError(LedgerError):
    """Code asked for the service's resources as a type they are not. A bug in the code that asks."""

    code: ClassVar[str] = "unexpected_resources"

    def __init__(self, *, held: object, asked: type[object]) -> None:
        super().__init__(f"the service's resources are {type(held).__name__}, not {asked.__name__}")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Runtime[S: ServiceSettings]:
    """What a running service's routes, handlers, workers, checks and gRPC methods work with."""

    settings: S
    database: Database
    nats: Client
    jetstream: JetStreamContext
    outbox: Outbox
    """Where routes and handlers store the events they announce, in their own transaction."""
    tracing: Tracing
    registry: CollectorRegistry
    """The registry ``/metrics`` exports, for metrics of the service's own."""
    interruptions: Interruptions
    """Told when the connection to NATS is lost and when it is back (``ledger_platform.interruptions``)."""
    opened: object = None
    """What ``Interfaces.resources`` yielded; read it typed with ``resources``."""

    def resources[R](self, kind: type[R]) -> R:
        """The service's resources (what ``Interfaces.resources`` yielded), as the ``kind`` they are.

        ``UnexpectedResourcesError`` when they are not of that kind (or the service opened none).
        """
        if not isinstance(self.opened, kind):
            raise UnexpectedResourcesError(held=self.opened, asked=kind)
        return self.opened


type RuntimeDependency[S: ServiceSettings] = Callable[[], Coroutine[object, object, Runtime[S]]]
"""What a route declares to be handed its service's runtime: ``Annotated[Runtime[S], Depends(runtime)]``.

Asynchronous, so that FastAPI resolves it on the event loop rather than in its thread pool. While the service
is not running it raises ``ServiceNotRunningError``, which a client gets as a 503."""
type HttpRoutes[S: ServiceSettings] = Callable[[RuntimeDependency[S]], Sequence[APIRouter]]
"""Makes a service's HTTP routes, given the dependency that hands them the runtime (called once, at build)."""
type ConsumerFactory[S: ServiceSettings] = Callable[[Runtime[S]], Sequence[ConsumerSpec]]
"""The consumers of a service, made once it runs, so that their handlers can use what it runs on."""
type GrpcServices[S: ServiceSettings] = Callable[[Runtime[S], grpc.aio.Server], None]
"""Adds a service's gRPC services to its server, once it runs."""
type ResourcesOpener[S: ServiceSettings] = Callable[[S], AbstractAsyncContextManager[object]]
"""Opens what a service talks to beside the chassis (HTTP clients, gRPC channels), and closes it on exit."""
type ReadinessCheck[S: ServiceSettings] = Callable[[Runtime[S]], Awaitable[object]]
"""Returns when what it checks is ready, raises when not; handed the runtime of the running service."""
type WorkerFactory[S: ServiceSettings] = Callable[[Runtime[S]], Mapping[str, Work]]
"""The background workers of a service by name, made once it runs."""


def _no_routes[S: ServiceSettings](runtime: RuntimeDependency[S]) -> Sequence[APIRouter]:
    del runtime
    return ()


def _no_consumers[S: ServiceSettings](runtime: Runtime[S]) -> Sequence[ConsumerSpec]:
    del runtime
    return ()


def _no_workers[S: ServiceSettings](runtime: Runtime[S]) -> Mapping[str, Work]:
    del runtime
    return {}


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Interfaces[S: ServiceSettings]:
    """Where a service meets the rest of the system.

    The HTTP routes and gRPC services clients reach, the resources it reaches others with, the readiness
    checks of those, and the metrics it is watched by.
    """

    http: HttpRoutes[S] = _no_routes
    """Called when the service is built, and complete then: a route added later is measured as
    ``unregistered``."""
    grpc: GrpcServices[S] | None = None
    """``None``: the service runs no gRPC server."""
    resources: ResourcesOpener[S] | None = None
    """An async context manager that opens what the service talks to beside the chassis (an HTTP client, gRPC
    channels) and yields one object holding them, a dataclass of the service's own: entered at start after
    NATS and the streams, and exited at stop after the gRPC server and the workers have stopped and before
    NATS drains. Code reads it typed with ``runtime.resources(Kind)``. ``None``: the service opens nothing.

    The exit runs in another task than the entry (the stop bounds it with ``resources_close_timeout``), so a
    resource must not hold a scope bound to the task that entered it across its ``yield``: an
    ``asyncio.timeout``, a ``TaskGroup`` or an anyio cancel scope fails at the close. Clients (an HTTP client,
    a gRPC channel) do not."""
    checks: Mapping[str, ReadinessCheck[S]] = field(default_factory=dict[str, ReadinessCheck[S]])
    """Readiness checks of the service's own, by name, beside the chassis's (``database``, ``nats``,
    ``workers``, ``schema``, whose names they cannot take): each is handed the runtime and held to the same
    time limit; while the service is not running it fails.

    A check must never use the pool (``runtime.database.transaction``): every request may hold a pooled
    connection, and a check waiting for one would take every busy replica out of rotation. It reads on a
    connection of its own (``runtime.database.probing()``), and a check of what does not change back once it
    holds (a schema migrated, a table created) remembers that it passed and stops asking, as the chassis's
    ``schema`` check and ``ledger_platform.migrations.SchemaReadiness`` do: probes come every few seconds."""
    registry: CollectorRegistry | None = None
    """Where the service's metrics are kept, which ``/metrics`` exports to Prometheus; ``None``: a new one.

    Given one, metrics made before the service is built (those of an object its routes are built with, such as
    ``PublishedKeys``'s) are exported beside the service's own. It must be the service's alone: the service
    registers its metrics in it, and a second service on it would register them again."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Events[S: ServiceSettings]:
    """What a service publishes to and what it consumes."""

    streams: Sequence[StreamSpec] = ()
    """The streams of the events it publishes, created or updated at start (``DEAD_LETTERS`` always is)."""
    consumers: ConsumerFactory[S] = _no_consumers
    workers: WorkerFactory[S] = _no_workers
    """Background workers of the service's own, by name, made once it runs: supervised like the relay and the
    consumers (started again after a failure, for good after an ``InvalidOptionError``) and stopped with
    them. A name the chassis uses (``outbox relay``, ``consumer <name>``) is refused at start."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceLimits:
    """How long a service waits on PostgreSQL, NATS and its own workers, and PostgreSQL on the service.

    The relay waits for JetStream's acknowledgements of a batch inside the transaction that locks its rows,
    so ``publish_timeout`` may take at most half of ``database.idle_in_transaction_session_timeout``
    (``_PUBLISH_SHARE_OF_IDLE``): PostgreSQL ends a session idle in its transaction for that long, and the
    pass needs the rest for its statements. That relation is checked here; the outbox checks its own rules for
    the timeout (positive, at most ``MAX_PUBLISH_TIMEOUT``) when the service starts.

    A stop takes at most ``stop_budget`` seconds, whatever hangs: the gRPC grace, the workers' grace, the
    resources' close, the NATS drain and the span flush, one after the other (closing the pool takes
    milliseconds). Kubernetes kills a pod ``terminationGracePeriodSeconds`` after it asked it to stop (30 by
    default), and the ASGI server drains its HTTP requests before the chassis's stop begins: that grace must
    exceed the server's own drain plus ``stop_budget`` (25 seconds by default).
    """

    database: DatabaseLimits = DEFAULT_LIMITS
    pool_size: int = DEFAULT_POOL_SIZE
    """Connections the PostgreSQL pool keeps; as many again are opened for bursts."""
    publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT
    """Seconds the relay waits for the acknowledgements of a batch."""
    nats_connect_timeout: float = DEFAULT_NATS_CONNECT_TIMEOUT
    """Seconds the first connection to NATS is retried before the start fails; reconnections are unlimited."""
    grpc_stop_grace: float = DEFAULT_GRPC_STOP_GRACE
    """Seconds the gRPC calls in flight are given to finish when the service stops; then they are cut."""
    worker_stop_grace: float = DEFAULT_WORKER_STOP_GRACE
    """Seconds the workers are given to stop, together; one that takes longer is logged and left behind."""
    resources_close_timeout: float = DEFAULT_RESOURCES_CLOSE_TIMEOUT
    """Seconds the service's own resources are given to close; one that takes longer is logged and
    abandoned (``resources.close_abandoned``)."""
    nats_drain_timeout: float = DEFAULT_NATS_DRAIN_TIMEOUT
    """Seconds the connection to NATS is given to drain; then it is closed."""
    span_flush_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
    """Seconds the pending spans are given to reach the collector, and each export to it is given."""

    def __post_init__(self) -> None:
        require_at_least(InvalidServiceOptionError, "pool_size", self.pool_size)
        waits = {
            "nats_connect_timeout": self.nats_connect_timeout,
            "grpc_stop_grace": self.grpc_stop_grace,
            "worker_stop_grace": self.worker_stop_grace,
            "resources_close_timeout": self.resources_close_timeout,
            "nats_drain_timeout": self.nats_drain_timeout,
            "span_flush_timeout": self.span_flush_timeout,
        }
        for option, seconds in waits.items():
            require_seconds(InvalidServiceOptionError, option, seconds)
        idle = self.database.idle_in_transaction_session_timeout.total_seconds()
        ceiling = idle * _PUBLISH_SHARE_OF_IDLE
        if not self.publish_timeout <= ceiling:  # NaN included
            raise InvalidServiceOptionError(
                option="publish_timeout",
                requirement=(
                    f"at most {ceiling} seconds ({_PUBLISH_SHARE_OF_IDLE:.0%} of the database's "
                    "idle_in_transaction_session_timeout)"
                ),
                value=self.publish_timeout,
            )

    @property
    def stop_budget(self) -> float:
        """The longest a stop takes, in seconds, whatever hangs (see the class)."""
        return (
            self.grpc_stop_grace
            + self.worker_stop_grace
            + self.resources_close_timeout
            + self.nats_drain_timeout
            + self.span_flush_timeout
        )


_DEFAULT_LIMITS: Final = ServiceLimits()


def _exporter(settings: ServiceSettings, limits: ServiceLimits) -> SpanExporter | None:
    """An OTLP/gRPC exporter to the collector the settings name; none when they name none.

    Each export is held to the time limit of the flush at the stop.
    """
    endpoint = settings.otlp_endpoint
    return (
        None if endpoint is None else OTLPSpanExporter(endpoint=endpoint, timeout=limits.span_flush_timeout)
    )


@final
class _ConnectionLog:
    """What happens to a connection to NATS, in the service's log: what failed, never what was being sent.

    While NATS is unreachable every attempt to reconnect fails: the first error is logged, then one a minute
    (``NATS_ERRORS_LOGGED_EVERY``) saying how many were not, and the reconnection says how many were left
    out since. A disconnection (not a close on purpose) and a reconnection are told to ``interruptions``.
    """

    __slots__ = ("_client", "_errors", "_interruptions")

    def __init__(self, client: Client, interruptions: Interruptions) -> None:
        self._client = client
        self._interruptions = interruptions
        self._errors = Throttled(_logger, "nats.error", every=NATS_ERRORS_LOGGED_EVERY)

    async def failed(self, error: Exception) -> None:
        self._errors.warning(error=error_type(error), detail=truncate(str(error), _DETAIL_LIMIT))

    async def disconnected(self) -> None:
        if not self._client.is_closed:  # closing it on purpose is no interruption
            _logger.warning("nats.disconnected")
            self._interruptions.interrupted()

    async def reconnected(self) -> None:
        unlogged = self._errors.settle()
        _logger.info("nats.reconnected", **({"unlogged_errors": unlogged} if unlogged else {}))
        self._interruptions.interrupted()


async def _connect(
    client: Client, settings: ServiceSettings, limit: float, interruptions: Interruptions
) -> None:
    """Connect ``client`` within ``limit`` seconds, or raise ``NatsUnreachableError``.

    Once connected, the client reconnects for as long as it takes, until it is closed. The caller closes it in
    every case: a connection cut short by the timeout leaves an attempt in flight.
    """
    log = _ConnectionLog(client, interruptions)
    try:
        async with asyncio.timeout(limit):
            await client.connect(
                servers=settings.nats_url.get_secret_value(),
                name=settings.service_name,
                max_reconnect_attempts=_UNLIMITED,
                reconnect_time_wait=_RECONNECT_WAIT,
                error_cb=log.failed,
                disconnected_cb=log.disconnected,
                reconnected_cb=log.reconnected,
            )
    except TimeoutError as error:
        raise NatsUnreachableError(limit) from error


async def _disconnect(client: Client, limit: float) -> None:
    """Drain a live connection within ``limit`` seconds, then close it; close any other at once.

    Draining sends what the connection buffered and ends its subscriptions.

    Draining refuses a connection that is closed (someone closed it), reconnecting (NATS is unreachable) or
    still connecting (the start was cut short): closing stops the attempts, and leaves a closed one as it is.
    The settings refuse a NATS URL the client could not parse, so ``connect`` has always begun by now. A
    server that stopped answering would keep nats-py draining for forty seconds (its flush, then its drain
    timeout): past ``limit`` the drain is logged as ``nats.drain_timed_out`` and the connection closed.
    """
    draining: asyncio.Task[None] | None = None
    try:
        if client.is_connected:
            draining = asyncio.create_task(client.drain())
            _, unfinished = await asyncio.wait((draining,), timeout=limit)
            if unfinished:
                _logger.warning("nats.drain_timed_out", timeout=limit)
    finally:
        await client.close()
        if draining is not None:
            await _finish(draining)


async def _finish(draining: asyncio.Task[None]) -> None:
    """Let a drain the close cut short end, and take how it ended, error or not: nobody else awaits it."""
    draining.cancel()
    await asyncio.gather(draining, return_exceptions=True)


async def _open_resources[R](
    lifecycle: Lifecycle, resources: AbstractAsyncContextManager[R], limit: float
) -> R:
    """Enter ``resources`` now; at the stop, leave them within ``limit`` seconds.

    A close that hangs would hold the whole stop (the pool, NATS and the spans come after it): past ``limit``
    it is cancelled, logged as ``resources.close_abandoned``, and left behind if it ignores the cancellation.
    A close that fails within the limit fails the stop, like any other part's.
    """
    stack = AsyncExitStack()
    opened = await stack.enter_async_context(resources)
    lifecycle.on_close(partial(_close_within, stack, limit))
    return opened


async def _close_within(resources: AsyncExitStack, limit: float) -> None:
    closing = asyncio.create_task(resources.aclose())
    _, unfinished = await asyncio.wait((closing,), timeout=limit)
    if unfinished:
        ABANDONED_CLOSES.add(closing)
        closing.add_done_callback(_note_abandoned_close)
        closing.cancel()
        _logger.error("resources.close_abandoned", timeout=limit)
        return
    await closing


def _note_abandoned_close(closing: asyncio.Task[None]) -> None:
    """How an abandoned close ended, when it did: its error is taken and logged, not reported as lost."""
    ABANDONED_CLOSES.discard(closing)
    if not closing.cancelled() and (error := closing.exception()) is not None:
        _logger.warning("resources.close_failed_after_abandon", exc_info=error)


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
        "_schema_ready",
        "_settings",
        "_started",
        "_supervisor",
        "_tracing",
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
        given = self._interfaces.registry
        self._registry = CollectorRegistry() if given is None else given
        self._tracing = Tracing.create(
            service_name=settings.service_name, exporter=_exporter(settings, limits)
        )
        self._supervisor = Supervisor(registry=self._registry, permanent=_PERMANENT)
        self._runtime: Runtime[S] | None = None
        self._grpc_port: int | None = None
        self._started = False
        self._schema_ready = False
        ProcessCollector(registry=self._registry)
        PlatformCollector(registry=self._registry)
        GCCollector(registry=self._registry)
        health = Health()
        health.add("database", self._database_answers)
        health.add("nats", self._nats_is_connected)
        health.add("workers", self._no_worker_is_dead)
        health.add("schema", self._chassis_tables_exist)
        for name, check in self._interfaces.checks.items():
            health.add(name, partial(self._check_with_runtime, check))
        health.add_liveness("workers", self._workers_live)
        self._app = create_app(
            info=ApiInfo(title=settings.service_name, version=version),
            observability=Observability(
                health=health, registry=self._registry, tracer_provider=self._tracing.provider
            ),
            lifespan=self._lifespan,
            routers=self._interfaces.http(self._current_runtime),
        )

    @property
    def app(self) -> FastAPI:
        """The ASGI application to serve: its lifespan runs the service."""
        return self._app

    def runtime(self) -> Runtime[S]:
        """What the running service works with, for code that is not handed it.

        Routes are handed the asynchronous ``RuntimeDependency`` instead (see ``HttpRoutes``), consumers and
        gRPC services the runtime itself. Raises ``ServiceNotRunningError`` while the service is not running.
        """
        if self._runtime is None:
            raise ServiceNotRunningError
        return self._runtime

    @property
    def workers(self) -> tuple[asyncio.Task[Never], ...]:
        """The background workers in the order they started.

        The outbox relay, one per consumer, then the service's own.
        """
        return self._supervisor.workers

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
                pool_size=limits.pool_size,
                application_name=settings.service_name,
                limits=limits.database,
            )
        )
        connection = Client()
        # Closed in every case, so registered before it connects: an attempt cut short is closed too.
        lifecycle.on_close(partial(_disconnect, connection, limits.nats_drain_timeout))
        interruptions = Interruptions()
        await _connect(connection, settings, limits.nats_connect_timeout, interruptions)
        jetstream = JetStreamContext(connection)
        for stream in (DEAD_LETTERS, *self._events.streams):
            await ensure_stream(jetstream, stream)
        opener = self._interfaces.resources
        opened = (
            None
            if opener is None
            else await _open_resources(lifecycle, opener(settings), limits.resources_close_timeout)
        )
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
            interruptions=interruptions,
            opened=opened,
        )
        # One stop for every worker started from here on, before NATS drains and after gRPC stops.
        lifecycle.on_close(partial(self._supervisor.stop, limits.worker_stop_grace))
        relay = partial(outbox.run, database, jetstream, streams=self._events.streams)
        _ = self._supervisor.start("outbox relay", relay)
        metrics = ConsumerMetrics(self._registry)
        for spec in self._events.consumers(runtime):
            consumer = Consumer(spec, database=database, tracing=tracing, metrics=metrics)
            _ = self._supervisor.start(f"consumer {spec.name}", partial(consumer.run, jetstream))
        for name, work in self._events.workers(runtime).items():
            _ = self._supervisor.start(name, work)
        services = self._interfaces.grpc
        if services is not None:
            self._grpc_port = await lifecycle.enter(
                serve_grpc(
                    port=settings.grpc_port,
                    register=partial(services, runtime),
                    registry=self._registry,
                    grace=limits.grpc_stop_grace,
                )
            )
        return runtime

    async def _current_runtime(self) -> Runtime[S]:
        """The ``RuntimeDependency`` routes are handed."""
        return self.runtime()

    async def _export_pending_spans(self) -> None:
        await asyncio.to_thread(self._tracing.shutdown, self._limits.span_flush_timeout)

    async def _database_answers(self) -> None:
        await self.runtime().database.ping()

    async def _chassis_tables_exist(self) -> None:
        """Look for the tables until they are there.

        Then never again: no migration drops them, and every probe would otherwise open a connection of its
        own to ask.
        """
        database = self.runtime().database  # raises while the service is not running
        if self._schema_ready:
            return
        missing = await database.missing_tables(tuple(CHASSIS_METADATA.tables))
        if missing:
            raise _ChassisTablesMissingError(missing)
        self._schema_ready = True

    async def _check_with_runtime(self, check: ReadinessCheck[S]) -> None:
        await check(self.runtime())

    async def _nats_is_connected(self) -> None:
        if not self.runtime().nats.is_connected:
            raise _NatsDisconnectedError

    async def _no_worker_is_dead(self) -> None:
        self.runtime()  # raises while the service is not running
        dead = self._supervisor.dead()
        if dead:
            raise _WorkerStoppedError(dead)

    def _workers_live(self) -> bool:
        return not self._supervisor.dead()
