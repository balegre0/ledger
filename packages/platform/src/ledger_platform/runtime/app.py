"""An app (§3.3, §3.10): the composition root every console script runs, in two phases.

Assemble (at construction, no I/O): load the platform's and the modules' manifests, the regimes and the
channels (entry points); make the pool object and one ``BoundDatabase`` per schema; assemble the modules in
dependency order, each reading only the APIs, extensions and ports of its declared dependencies; then the
channels, with the ports modules provide and the archive the settings configure (``runtime.archiving``,
bound as ``ArchivePort``); then merge what they handed back and check it against the manifests
(every declared consumer and job is provided, nothing undeclared is: ``runtime.assembly``). Start
(``asgi()``'s lifespan, or ``migrate()``): open what the role needs, nothing more.

- ``api``: the routers of every module mounted in ``api`` (and the channels' public routes); the identity
  provider's keys as a resource. It refuses to start without the token settings.
- ``worker``: the fan-out, an inbox runner per consumer, a job runner per schema with jobs, the scheduler and
  the LISTEN connection, each supervised; ``/livez``, ``/readyz`` and ``/metrics`` on the worker's HTTP port.
  The fan-out covers the schemas of modules isolated in another role too; nothing else of the worker does.
- ``signer``: the modules isolated in it alone (``runtime.assembly``), their resources (a gRPC server), inbox
  runners, job runners and schedules, the scheduler and the LISTEN connection as the worker runs them, but no
  fan-out (the worker's moves its outbox); ``/livez``, ``/readyz`` and ``/metrics`` on its HTTP port.
- ``migrate``: every schema's revisions in order (platform first), the relay's grants, then the subscriptions
  of every schema and the routing version in one transaction.
- ``receiver``: refused until its modules exist (v2-M4).

Connections. Units go through ``LEDGER_DATABASE_URL``, which may be a transaction pooler's (§6.7): with
``LEDGER_DATABASE_POOLED`` psycopg prepares no statement on it. The after-commit NOTIFYs have a pool of their
own (one connection, short waits), so a busy pool never delays them and they never take a unit's connection.
The LISTEN connections of the worker and the signer, and ``ledger-migrate``, connect directly
(``LEDGER_DATABASE_DIRECT_URL``, which the worker and the signer require when they are made); the URLs are
secrets and never logged. ``migrate`` exports no spans.

An app starts once (``AppAlreadyStartedError`` for a second lifespan). It stops within ``STOP_BUDGET``
seconds, step by step, each step held to its share (``STOP``): the worker's runners are asked to stop (claim
nothing more, finish what they run, give back what they hold) and given ``drain`` seconds, then every worker
is cancelled (the runners give back what they still hold); then the resources, the pending NOTIFYs, the pools
and the spans still pending. A step that runs out of time is left behind and logged
(``app.stop_step_timed_out``), one that fails is logged (``app.stop_step_failed``): neither holds the rest.
Limit: a step left behind is cancelled but cannot be forced to end. One that ignores its cancellation runs on
(kept in ``_ABANDONED``) until the process exits, and may still hold a pool connection when the pools close
(``app.pools_closing`` logs how many are out); the process exiting is what ends it. Served by Granian, the
stop starts once the server has let its requests in flight finish, and both share the server's 30 s before it
kills the process: Granian bounds no request's time, so a route answers well within 5 s (long work is a job).
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Awaitable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool, QueuePool
lazy from fastapi import FastAPI
lazy from sqlalchemy import Connection

from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_platform.config.names import PLATFORM
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.database import Database, DatabaseLimits, Pooling
from ledger_platform.data.migrations import MIGRATION_LOCK_WAIT
from ledger_platform.data.notifier import Notifier
from ledger_platform.data.roles import SchemaRoles, identifier
from ledger_platform.events.dispatcher import Dispatcher
from ledger_platform.events.inbox import InboxRunner
from ledger_platform.events.subscriptions import bump_routing, sync_subscriptions
from ledger_platform.events.wakeups import Wakeups
from ledger_platform.http.app import ApiInfo, Observability, create_app
from ledger_platform.http.authentication import Authentication
from ledger_platform.jobs.runner import JobRunner
from ledger_platform.jobs.schedules import Scheduler
from ledger_platform.observability.health import Health
from ledger_platform.observability.logs import configure_logging
from ledger_platform.observability.tracing import DEFAULT_SHUTDOWN_TIMEOUT, Tracing
from ledger_platform.runtime.archiving import archive_of
from ledger_platform.runtime.assembly import Shared, assemble
from ledger_platform.runtime.registries import installed
from ledger_platform.runtime.supervision import Supervisor
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.config.manifest import AppRole, PackageManifest
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_platform.runtime.assembly import Member
lazy from ledger_platform.runtime.registries import Discover

NOTIFY_LIMITS: Final = DatabaseLimits(
    connect_timeout=timedelta(seconds=2),
    statement_timeout=timedelta(seconds=2),
    lock_timeout=timedelta(seconds=1),
    idle_in_transaction_session_timeout=timedelta(seconds=10),
    pool_timeout=timedelta(seconds=1),
)
"""The NOTIFYs' pool waits little: a NOTIFY that cannot go is dropped, and the one-second poll covers it."""
POOL_HEADROOM: Final = 2
"""Connections beyond what a role runs: readiness checks, a request or a renewal at a busy moment."""
WORKER_LOOPS: Final = 2
"""The worker's fan-out and scheduler, each holding a connection while it runs a pass."""
SIGNER_LOOPS: Final = 1
"""The signer's scheduler: the worker fans its outbox out."""
STOP_BUDGET: Final = 25.0
"""Seconds an app takes to stop at most: inside an orchestrator's usual 30-second grace."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class StopTimes:
    """Each stop step's share of ``STOP_BUDGET``, in seconds."""

    drain: float = 8.0
    """The runners finish what they run, having been asked to stop."""
    workers: float = 4.0
    """Every worker cancelled, the runners giving back what they hold."""
    resources: float = 5.0
    """The modules' resources and the identity provider's keys client, shared out among them."""
    notifications: float = 1.0
    pools: float = 3.0
    spans: float = 4.0

    @property
    def total(self) -> float:
        """The whole stop, at most."""
        return self.drain + self.workers + self.resources + self.notifications + self.pools + self.spans


STOP: Final = StopTimes()
_ABANDONED: Final[set[asyncio.Future[object]]] = set()
"""Stop steps left behind: kept until they end, so that they are not collected while they run."""
_logger = structlog.stdlib.get_logger(__name__)
_NOT_BUILT: Final = frozenset({"receiver"})
_LOOPS: Final[Mapping[AppRole, int]] = {"worker": WORKER_LOOPS, "signer": SIGNER_LOOPS}
"""The roles that run consumers, jobs and schedules (they listen for wake-ups), and their loops besides."""
_PERMANENT: Final = (InvalidOptionError,)
"""Failures of a worker that no restart can fix."""
_SET_LOCK_TIMEOUT: Final = text("SELECT set_config('lock_timeout', :wait, true)")


class RoleNotBuiltError(LedgerError):
    """An app of a role whose modules do not exist yet."""

    code: ClassVar[str] = "role_not_built"

    def __init__(self, role: str) -> None:
        super().__init__(f"the {role} app is not built yet: its modules come with a later milestone")


class RoleMismatchError(LedgerError):
    """An app asked to do what its role does not (an ``api`` asked to migrate, ``migrate`` asked to serve)."""

    code: ClassVar[str] = "app_role_mismatch"

    def __init__(self, role: str, operation: str) -> None:
        super().__init__(f"the {role} app does not {operation}")


class PoolTooSmallError(LedgerError):
    """A pool smaller than what the app's role may hold at once: a unit would wait for a connection."""

    code: ClassVar[str] = "pool_too_small"

    def __init__(self, role: str, size: int, need: PoolNeed) -> None:
        pool = f"the {role}'s pool of {size} connections (LEDGER_DATABASE_POOL_SIZE)"
        jobs = f"{need.jobs} for jobs (their concurrency)"
        renewals = f"{need.renewals} for lease renewals (running jobs, outside consumers)"
        parts = f"{jobs}, {renewals}, {need.runners} for its runners, {need.headroom}"
        super().__init__(f"{pool} is smaller than the {need.total} it may hold at once: {parts} of headroom")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PoolNeed:
    """The connections an app's role may hold at once (§6.7): what its pool must keep."""

    jobs: int = 0
    """Every job kind's ``concurrency``: a running job holds its unit's connection."""
    renewals: int = 0
    """One per running job and per outside consumer too: its lease is renewed in a unit of its own while its
    handler holds one (an outside handler, through the facades it calls)."""
    runners: int = 0
    """One per inbox runner and per job runner (claims, outcomes), the fan-out's and the scheduler's."""
    headroom: int = POOL_HEADROOM

    @property
    def total(self) -> int:
        """The pool's size, at least."""
        return self.jobs + self.renewals + self.runners + self.headroom


def pool_need(role: AppRole, members: Sequence[Member]) -> PoolNeed:
    """What ``role`` holds at once.

    The worker and the signer run their members' jobs and runners (a member relayed only has none); the
    others, requests.
    """
    loops = _LOOPS.get(role)
    if loops is None:
        return PoolNeed()
    jobs = sum(decl.concurrency for member in members for decl, _ in member.kinds.values())
    outside = sum(consumer.outside for member in members for consumer in member.consumers)
    return PoolNeed(
        jobs=jobs,
        renewals=jobs + outside,
        runners=sum(len(member.consumers) + bool(member.kinds) for member in members) + loops,
    )


class DirectUrlMissingError(LedgerError):
    """The worker, the signer or ``ledger-migrate`` without ``LEDGER_DATABASE_DIRECT_URL`` (LISTEN, locks)."""

    code: ClassVar[str] = "direct_url_missing"

    def __init__(self, role: str) -> None:
        super().__init__(f"the {role} app connects directly to PostgreSQL: set LEDGER_DATABASE_DIRECT_URL")


class AppAlreadyStartedError(LedgerError):
    """A second lifespan of one app: its workers, pools and resources start once."""

    code: ClassVar[str] = "app_already_started"

    def __init__(self) -> None:
        super().__init__("an app starts once: make another App for another lifespan")


class SchemaUnknownError(LedgerError):
    """A schema no package of the app owns."""

    code: ClassVar[str] = "schema_unknown"

    def __init__(self, schema: str) -> None:
        super().__init__(f"no package of this app owns schema {schema!r}")


class NotListeningError(LedgerError):
    """The LISTEN connection of the worker or the signer is down (readiness)."""

    code: ClassVar[str] = "listen_down"

    def __init__(self) -> None:
        super().__init__("the app does not listen for wake-ups")


class WorkersDownError(LedgerError):
    """The workers of the worker or the signer are not running, or some ended for good (readiness)."""

    code: ClassVar[str] = "workers_down"

    def __init__(self, dead: tuple[str, ...]) -> None:
        super().__init__(f"workers not running: {', '.join(dead) or 'all of them (not started)'}")


def _registry() -> CollectorRegistry:
    """The app's metrics, the process's own included."""
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    return registry


def _forget(step: asyncio.Future[object]) -> None:
    """A step left behind has ended: let it go, its outcome taken (it was logged as timed out)."""
    _ABANDONED.discard(step)
    _ = step.cancelled() or step.exception()


async def _within(step: str, seconds: float, work: Awaitable[object]) -> None:
    """Run ``work`` for at most ``seconds``; past that it is cancelled and left behind. Nothing is raised."""
    running = asyncio.ensure_future(work)
    done, _ = await asyncio.wait({running}, timeout=seconds)
    if not done:
        running.cancel()
        _ABANDONED.add(running)
        running.add_done_callback(_forget)
        _logger.error("app.stop_step_timed_out", step=step, timeout=seconds)
        return
    error = running.exception()
    if error is not None:
        _logger.error("app.stop_step_failed", step=step, exc_info=error)


def _checked_out(database: Database) -> int:
    """Connections of ``database``'s pool in use now."""
    pool = database.engine.pool
    return pool.checkedout() if isinstance(pool, QueuePool) else 0


def _route(url: str, declared: Mapping[str, Mapping[str, frozenset[str]]]) -> None:
    """One transaction as ``ledger_migrator``: each schema's subscriptions as its owner, then the routing.

    A fan-out reloads its routing when the version moves, so it never sees one schema's new subscriptions
    without the others'. Two migrations at once wait for each other's rows (at most ``MIGRATION_LOCK_WAIT``).
    """
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            wait = f"{MIGRATION_LOCK_WAIT // timedelta(milliseconds=1)}ms"
            connection.execute(_SET_LOCK_TIMEOUT, {"wait": wait})
            for schema in sorted(declared):  # one order for every migrator: their row locks never cross
                _become_owner(connection, schema)
                sync_subscriptions(connection, schema, declared[schema])
            _become_owner(connection, PLATFORM)
            bump_routing(connection)
    finally:
        engine.dispose()


def _become_owner(connection: Connection, schema: str) -> None:
    connection.execute(text(f"SET LOCAL ROLE {identifier(SchemaRoles(schema=schema).owner)}"))


@final
class App:
    """The app of one role (see the module). It is built once per process; ``asgi()`` is its served app."""

    __slots__ = (
        "_assembled",
        "_authentication",
        "_database",
        "_notifications",
        "_registry",
        "_role",
        "_runners",
        "_running",
        "_served",
        "_settings",
        "_signals",
        "_started",
        "_supervisor",
        "_tracing",
        "_version",
        "_wakeups",
    )

    def __init__(
        self,
        role: AppRole,
        *,
        settings: Settings,
        version: str = "0.0.0",
        discover: Discover = installed,
        clock: Clock = utc_now,
    ) -> None:
        if role in _NOT_BUILT:
            raise RoleNotBuiltError(role)
        if role in _LOOPS and settings.database_direct_url is None:
            raise DirectUrlMissingError(role)
        self._role = role
        self._settings = settings
        self._version = version
        self._registry = _registry()
        self._authentication = Authentication.of(settings, self._registry, required=role == "api")
        configure_logging(settings, service=f"ledger-{role}")
        endpoint = None if role == "migrate" else settings.otlp_endpoint  # a one-shot job: no spans
        exporter = (
            None
            if endpoint is None
            else OTLPSpanExporter(endpoint=endpoint, timeout=DEFAULT_SHUTDOWN_TIMEOUT)
        )
        self._tracing = Tracing.create(service_name=f"ledger-{role}", exporter=exporter)
        url = settings.database_url.get_secret_value()
        pooled = settings.database_pooled
        name = f"ledger-{role}"
        self._database = Database.create(
            url,
            pool_size=settings.database_pool_size,
            application_name=name,
            pooling=Pooling(overflow=settings.database_pool_overflow, through_pooler=pooled),
        )
        self._notifications = Database.create(
            url,
            pool_size=1,
            application_name=f"{name}-notify",
            limits=NOTIFY_LIMITS,
            pooling=Pooling(through_pooler=pooled),
        )
        self._signals = Notifier(self._notifications)
        shared = Shared(
            role=role,
            environment=settings.environment,
            clock=clock,
            database=self._database,
            signals=self._signals,
            tracing=self._tracing,
            registry=self._registry,
            authentication=self._authentication,
            archive=archive_of(settings, clock=clock),
        )
        self._assembled = assemble(shared, discover)
        if (
            not self._assembled.modules
        ):  # nothing installed, or every module suspended: almost surely a mistake
            _logger.warning("app.no_modules", role=role)
        need = pool_need(role, self._assembled.members)
        if settings.database_pool_size < need.total:
            raise PoolTooSmallError(role, settings.database_pool_size, need)
        self._supervisor = Supervisor(registry=self._registry, permanent=_PERMANENT)
        self._wakeups: Wakeups | None = None
        self._runners: list[InboxRunner | JobRunner] = []
        self._started = False
        self._running = False
        self._served: FastAPI | None = None

    @property
    def modules(self) -> tuple[PackageManifest, ...]:
        """The modules' manifests, in assembly order."""
        return self._assembled.modules

    @property
    def members(self) -> tuple[Member, ...]:
        """What the role assembled: the platform, then the modules in assembly order, then the channels."""
        return self._assembled.members

    @property
    def migrations(self) -> tuple[SchemaMigrations, ...]:
        """Every schema's revisions, in migration order: the platform's, then each module's and channel's."""
        return tuple(member.migrations for member in self._assembled.members)

    def bound(self, schema: str) -> BoundDatabase:
        """The units of ``schema`` (``SchemaUnknownError`` if no package of the app owns it)."""
        found = self._assembled.bound.get(schema)
        if found is None:
            raise SchemaUnknownError(schema)
        return found

    async def aclose(self) -> None:
        """Close the app's pools, as its lifespan does when it ends: for an app that never started one."""
        await self._close_pools()

    def asgi(self) -> FastAPI:
        """The app to serve (``api``, ``worker``, ``signer``): its lifespan starts and stops the rest."""
        if self._role not in {"api", "worker", "signer"}:
            raise RoleMismatchError(self._role, "serve HTTP")
        if self._served is None:
            members = self._assembled.members
            self._served = create_app(
                info=ApiInfo(title=f"ledger-{self._role}", version=self._version),
                observability=Observability(
                    health=self._health(), registry=self._registry, tracer_provider=self._tracing.provider
                ),
                lifespan=self._lifespan,
                routers=[router for member in members for router in member.routers],
            )
        return self._served

    def migrate(self) -> None:
        """Every schema to head (platform first), the relay's grants, the subscriptions, the routing."""
        if self._role != "migrate":
            raise RoleMismatchError(self._role, "migrate")
        direct = self._settings.database_direct_url
        if direct is None:
            raise DirectUrlMissingError(self._role)
        url = direct.get_secret_value()
        members = self._assembled.members
        for member in members:
            member.migrations.upgrade(url)
        _route(
            url,
            {
                member.schema: {decl.consumer: frozenset(decl.types) for decl in member.events.consumes}
                for member in members
            },
        )

    def _health(self) -> Health:
        health = Health()
        health.add("database", self._database.ping)
        for member in self._assembled.members:
            if not member.relay_only:  # a schema its login cannot enter
                health.add(f"schema:{member.schema}", partial(member.migrations.readiness, self._database))
            for name, check in member.checks.items():
                health.add(f"{member.name}:{name}", check)
        if self._role in _LOOPS:
            health.add("listen", self._listening)
            health.add("workers", self._working)
            health.add_liveness("workers", self._alive)
        return health

    async def _listening(self) -> None:
        if self._wakeups is None or not self._wakeups.connected:
            raise NotListeningError

    async def _working(self) -> None:
        dead = self._supervisor.dead()
        if dead or not self._running:
            raise WorkersDownError(dead)

    def _alive(self) -> bool:
        return not self._supervisor.dead()

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncGenerator[None]:
        del app
        if self._started:
            raise AppAlreadyStartedError
        self._started = True
        self._tracing.register_globally()
        opened: list[AbstractAsyncContextManager[object]] = []
        try:
            resources = [resource for member in self._assembled.members for resource in member.resources]
            for resource in (*resources, *self._authentication.resources()):
                await resource.__aenter__()
                opened.append(resource)
            if self._role in _LOOPS:
                self._start_workers()
            yield
        finally:
            await self._stop(opened)

    async def _stop(self, opened: Sequence[AbstractAsyncContextManager[object]]) -> None:
        """Stop within the budget, step by step (see the module); nothing here raises."""
        stop = STOP
        if self._running:
            self._running = False
            for runner in self._runners:
                runner.stop()
            drained = asyncio.gather(*(runner.drained.wait() for runner in self._runners))
            await _within("drain", stop.drain, drained)
            await _within("workers", stop.workers, self._supervisor.stop(stop.workers))
        share = stop.resources / max(len(opened), 1)
        for resource in reversed(opened):
            await _within("resource", share, resource.__aexit__(None, None, None))
        await _within("notifications", stop.notifications, self._signals.aclose())
        await _within("pools", stop.pools, self._close_pools())
        await _within("spans", stop.spans, asyncio.to_thread(self._tracing.shutdown, stop.spans * 0.9))

    async def _close_pools(self) -> None:
        """Both pools; how many connections are still out (a step left behind may hold one) is logged."""
        _logger.info(
            "app.pools_closing",
            checked_out=_checked_out(self._database) + _checked_out(self._notifications),
        )
        try:
            await self._notifications.aclose()
        finally:
            await self._database.aclose()

    def _start_workers(self) -> None:
        settings, supervisor, members = self._settings, self._supervisor, self._assembled.members
        self._running = True  # from the first worker on: a start that fails halfway is stopped all the same
        wakeups = self._wakeups = Wakeups(settings.listen_url.get_secret_value())
        _ = supervisor.start("wakeups", wakeups.run)
        # The worker fans out every schema, relayed ones too: the signer's outbox is moved by the worker's.
        if self._role == "worker":
            dispatcher = Dispatcher(
                self._database,
                schemas=tuple(member.schema for member in members),
                signals=self._signals,
                registry=self._registry,
            )
            _ = supervisor.start("dispatcher", partial(dispatcher.run, wakeups))
        for member in members:
            database = self._assembled.bound[member.schema]
            for consumer in member.consumers:
                runner = InboxRunner(
                    database=database, consumer=consumer, tracing=self._tracing, registry=self._registry
                )
                self._runners.append(runner)
                _ = supervisor.start(f"inbox {consumer.name}", partial(runner.run, wakeups))
            if member.kinds:
                jobs = JobRunner(
                    database=database, kinds=member.kinds, tracing=self._tracing, registry=self._registry
                )
                self._runners.append(jobs)
                _ = supervisor.start(f"jobs {member.schema}", jobs.run)
        scheduler = Scheduler(platform=self._assembled.bound[PLATFORM], schedules=self._assembled.schedules)
        _ = supervisor.start("scheduler", scheduler.run)
