"""An app (§3.3, §3.10): the composition root every console script runs, in two phases.

Assemble (at construction, no I/O): load the platform's and the modules' manifests, the regimes and the
channels (entry points); make the pool object and one ``BoundDatabase`` per schema; assemble the modules in
dependency order, each reading only the APIs, extensions and ports of its declared dependencies; then the
channels, with the ports modules provide; then merge what they handed back and check it against the manifests
(every declared consumer and job is provided, nothing undeclared is: ``runtime.assembly``). Start
(``asgi()``'s lifespan, or ``migrate()``): open what the role needs, nothing more.

- ``api``: the routers of every module mounted in ``api`` (and the channels' public routes); the identity
  provider's keys as a resource. It refuses to start without the token settings.
- ``worker``: the fan-out, an inbox runner per consumer, a job runner per schema with jobs, the scheduler and
  the LISTEN connection, each supervised; ``/livez``, ``/readyz`` and ``/metrics`` on the worker's HTTP port.
- ``migrate``: every schema's revisions in order (platform first), the relay's grants, then the subscriptions
  of every schema and the routing version in one transaction.
- ``signer``, ``receiver``: refused until their modules exist (v2-M2, v2-M4).

Connections. Units go through ``LEDGER_DATABASE_URL``, which may be a transaction pooler's (§6.7): when a
direct URL is set apart from it, the pooled URL is taken to go through a pooler and psycopg prepares no
statement on it. The after-commit NOTIFYs have a pool of their own (one connection, short waits), so a busy
pool never delays them and they never take a unit's connection. The worker's LISTEN connection and
``ledger-migrate`` connect directly (``LEDGER_DATABASE_DIRECT_URL``); the URLs are secrets and never logged.

Stop, in the order v1's service stopped (without NATS): the workers (within a grace), the resources, the
pending NOTIFYs, the pools, then the spans still pending.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from functools import partial
from typing import ClassVar, Final, final
lazy from collections.abc import AsyncGenerator, Mapping

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
lazy from fastapi import FastAPI
lazy from sqlalchemy import Connection

from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.database import Database, DatabaseLimits
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
from ledger_platform.runtime.assembly import PLATFORM, Shared, assemble
from ledger_platform.runtime.registries import installed
from ledger_platform.runtime.supervision import Supervisor
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.manifest import AppRole, PackageManifest
lazy from ledger_platform.runtime.registries import Discover

NOTIFY_LIMITS: Final = DatabaseLimits(
    connect_timeout=timedelta(seconds=2),
    statement_timeout=timedelta(seconds=2),
    lock_timeout=timedelta(seconds=1),
    idle_in_transaction_session_timeout=timedelta(seconds=10),
    pool_timeout=timedelta(seconds=1),
)
"""The NOTIFYs' pool waits little: a NOTIFY that cannot go is dropped, and the one-second poll covers it."""
WORKER_STOP_GRACE: Final = 10.0
"""Seconds the workers are given, all together, to end when the app stops."""
_NOT_BUILT: Final = frozenset({"signer", "receiver"})
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


class DirectUrlMissingError(LedgerError):
    """``ledger-migrate`` without ``LEDGER_DATABASE_DIRECT_URL``: migrations hold session locks."""

    code: ClassVar[str] = "direct_url_missing"

    def __init__(self) -> None:
        super().__init__("migrations connect directly to PostgreSQL: set LEDGER_DATABASE_DIRECT_URL")


class SchemaUnknownError(LedgerError):
    """A schema no package of the app owns."""

    code: ClassVar[str] = "schema_unknown"

    def __init__(self, schema: str) -> None:
        super().__init__(f"no package of this app owns schema {schema!r}")


class NotListeningError(LedgerError):
    """The worker's LISTEN connection is down (readiness)."""

    code: ClassVar[str] = "listen_down"

    def __init__(self) -> None:
        super().__init__("the worker does not listen for wake-ups")


class WorkersDownError(LedgerError):
    """The worker's workers are not running, or some ended for good (readiness)."""

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
            for schema, consumers in declared.items():
                _become_owner(connection, schema)
                sync_subscriptions(connection, schema, consumers)
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
        "_running",
        "_served",
        "_settings",
        "_signals",
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
        self._role = role
        self._settings = settings
        self._version = version
        self._registry = _registry()
        self._authentication = Authentication.of(settings, self._registry, required=role == "api")
        configure_logging(settings, service=f"ledger-{role}")
        endpoint = settings.otlp_endpoint
        exporter = (
            None
            if endpoint is None
            else OTLPSpanExporter(endpoint=endpoint, timeout=DEFAULT_SHUTDOWN_TIMEOUT)
        )
        self._tracing = Tracing.create(service_name=f"ledger-{role}", exporter=exporter)
        url = settings.database_url.get_secret_value()
        direct = settings.database_direct_url
        prepared = direct is None or direct.get_secret_value() == url
        name = f"ledger-{role}"
        self._database = Database.create(url, application_name=name, prepared_statements=prepared)
        self._notifications = Database.create(
            url,
            pool_size=1,
            application_name=f"{name}-notify",
            limits=NOTIFY_LIMITS,
            prepared_statements=prepared,
        )
        self._signals = Notifier(self._notifications)
        shared = Shared(
            role=role,
            settings=settings,
            clock=clock,
            database=self._database,
            signals=self._signals,
            tracing=self._tracing,
            registry=self._registry,
            authentication=self._authentication,
        )
        self._assembled = assemble(shared, discover)
        self._supervisor = Supervisor(registry=self._registry, permanent=_PERMANENT)
        self._wakeups: Wakeups | None = None
        self._running = False
        self._served: FastAPI | None = None

    @property
    def modules(self) -> tuple[PackageManifest, ...]:
        """The modules' manifests, in assembly order."""
        return self._assembled.modules

    def bound(self, schema: str) -> BoundDatabase:
        """The units of ``schema`` (``SchemaUnknownError`` if no package of the app owns it)."""
        found = self._assembled.bound.get(schema)
        if found is None:
            raise SchemaUnknownError(schema)
        return found

    def asgi(self) -> FastAPI:
        """The app to serve (roles ``api`` and ``worker``): its lifespan starts and stops everything else."""
        if self._role not in {"api", "worker"}:
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
            raise DirectUrlMissingError
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
            health.add(f"schema:{member.schema}", partial(member.migrations.readiness, self._database))
            for name, check in member.checks.items():
                health.add(f"{member.name}:{name}", check)
        if self._role == "worker":
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
        async with AsyncExitStack() as stack:
            stack.push_async_callback(self._flush_spans)  # the last: spans end until everything stopped
            self._tracing.register_globally()
            stack.push_async_callback(self._database.aclose)
            stack.push_async_callback(self._notifications.aclose)
            stack.push_async_callback(self._signals.aclose)
            for member in self._assembled.members:
                for resource in member.resources:
                    await stack.enter_async_context(resource)
            for resource in self._authentication.resources():
                await stack.enter_async_context(resource)
            if self._role == "worker":
                stack.push_async_callback(self._stop_workers)
                self._start_workers()
            yield

    def _start_workers(self) -> None:
        settings, supervisor, members = self._settings, self._supervisor, self._assembled.members
        wakeups = self._wakeups = Wakeups(settings.listen_url.get_secret_value())
        _ = supervisor.start("wakeups", wakeups.run)
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
                _ = supervisor.start(f"inbox {consumer.name}", partial(runner.run, wakeups))
            if member.kinds:
                jobs = JobRunner(
                    database=database, kinds=member.kinds, tracing=self._tracing, registry=self._registry
                )
                _ = supervisor.start(f"jobs {member.schema}", jobs.run)
        scheduler = Scheduler(platform=self._assembled.bound[PLATFORM], schedules=self._assembled.schedules)
        _ = supervisor.start("scheduler", scheduler.run)
        self._running = True

    async def _stop_workers(self) -> None:
        self._running = False
        await self._supervisor.stop(WORKER_STOP_GRACE)

    async def _flush_spans(self) -> None:
        await asyncio.to_thread(self._tracing.shutdown, DEFAULT_SHUTDOWN_TIMEOUT)
