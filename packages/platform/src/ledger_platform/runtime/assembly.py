"""An app's assembly (§3.10 steps 1-3): packages found, schemas claimed, modules then channels assembled.

What every package hands back is checked against its manifest before anything starts: a schema of its own
with its own migrations, a handler for every consumer and job kind it declares and none it does not, an
implementation for every extension point declared and a provider for every port declared, facades on what
other packages call. Every failure is a defect of a package or of the deployment, found at the start
(internal errors, never a client's).
"""

import graphlib
from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Iterable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

from pydantic import TypeAdapter
lazy from fastapi import APIRouter
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import LedgerError
from ledger_platform._manifest import MANIFEST as PLATFORM_MANIFEST
from ledger_platform.data.bound import BoundDatabase
from ledger_platform.data.migrations import platform_migrations
from ledger_platform.data.unit_of_work import JobPolicy
from ledger_platform.events.consumers import Consumer
from ledger_platform.jobs.maintenance import maintenance_jobs
from ledger_platform.jobs.schedules import Schedule
from ledger_platform.runtime.contexts import (
    AssembledChannels,
    ChannelContext,
    ModuleContext,
    Telemetry,
    api_view,
    bind_parts,
    extension_view,
)
from ledger_platform.runtime.keys import Directory
from ledger_platform.runtime.packs import Channels, load_modules
from ledger_platform.runtime.registries import Regimes
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.config.manifest import (
    AppRole,
    EventsSection,
    JobDecl,
    PackageManifest,
    ScheduleDecl,
)
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.database import Database
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_platform.data.notifier import Signals
lazy from ledger_platform.events.consumers import ConsumerSpec
lazy from ledger_platform.http.authentication import Authentication
lazy from ledger_platform.jobs.outcomes import JobHandler, JobSpec
lazy from ledger_platform.observability.health import Check
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_platform.runtime.contexts import ChannelPack, Module
lazy from ledger_platform.runtime.registries import Discover

PLATFORM: Final = "platform"
_CYCLE: Final = TypeAdapter(list[str])


class SchemaMissingError(LedgerError):
    """A module that owns no schema: every module owns one (§6.1)."""

    code: ClassVar[str] = "module_schema_missing"

    def __init__(self, module: str) -> None:
        super().__init__(f"module {module!r} owns no schema: its manifest has no [data] schema")


class DuplicateSchemaError(LedgerError):
    """Two packages claiming one schema."""

    code: ClassVar[str] = "schema_duplicate"

    def __init__(self, schema: str, first: str, second: str) -> None:
        super().__init__(f"schema {schema!r} is claimed by {first!r} and by {second!r}")


class ForeignSchemaError(LedgerError):
    """A package handing over the migrations of a schema it does not own."""

    code: ClassVar[str] = "schema_foreign"

    def __init__(self, package: str, schema: str) -> None:
        super().__init__(f"{package!r} hands over the migrations of schema {schema!r}, which it does not own")


class MigrationsMissingError(LedgerError):
    """A package with a schema and no migrations for it."""

    code: ClassVar[str] = "migrations_missing"

    def __init__(self, package: str, schema: str) -> None:
        super().__init__(f"{package!r} hands over no migrations of its schema {schema!r}")


class ModuleMissingError(LedgerError):
    """A module depending on one that is not installed (or is suspended)."""

    code: ClassVar[str] = "module_missing"

    def __init__(self, module: str, dependency: str) -> None:
        super().__init__(
            f"module {module!r} depends on {dependency!r}, which is not installed or is suspended"
        )


class ModuleCycleError(LedgerError):
    """Modules whose dependencies form a cycle: none of them can assemble first."""

    code: ClassVar[str] = "module_cycle"

    def __init__(self, cycle: Sequence[str]) -> None:
        super().__init__(f"the modules' dependencies form a cycle: {' -> '.join(cycle)}")


class ConsumerUndeclaredError(LedgerError):
    """A handler handed over for a consumer the manifest does not declare."""

    code: ClassVar[str] = "consumer_undeclared"

    def __init__(self, package: str, consumer: str) -> None:
        super().__init__(f"{package!r} hands over consumer {consumer!r}, which its manifest does not declare")


class ConsumerNotProvidedError(LedgerError):
    """A declared consumer without a handler."""

    code: ClassVar[str] = "consumer_not_provided"

    def __init__(self, package: str, consumer: str) -> None:
        super().__init__(f"{package!r} declares consumer {consumer!r} and hands over no handler for it")


class JobUndeclaredError(LedgerError):
    """A handler handed over for a job kind the manifest does not declare."""

    code: ClassVar[str] = "job_undeclared"

    def __init__(self, package: str, kind: str) -> None:
        super().__init__(f"{package!r} hands over job kind {kind!r}, which its manifest does not declare")


class JobNotProvidedError(LedgerError):
    """A declared job kind without a handler."""

    code: ClassVar[str] = "job_not_provided"

    def __init__(self, package: str, kind: str) -> None:
        super().__init__(f"{package!r} declares job kind {kind!r} and hands over no handler for it")


class HandlerDuplicateError(LedgerError):
    """Two handlers of one consumer or one job kind."""

    code: ClassVar[str] = "handler_duplicate"

    def __init__(self, label: str, name: str) -> None:
        super().__init__(f"{label} {name!r} has two handlers")


class CronScheduleError(LedgerError):
    """A ``cron`` schedule: refused until a schedule needs one (Decision 15)."""

    code: ClassVar[str] = "schedule_cron_unsupported"

    def __init__(self, schedule: str) -> None:
        super().__init__(f"schedule {schedule!r} runs on cron, which the runtime does not run yet: use every")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Shared:
    """What every package's context is made of: the app's own."""

    role: AppRole
    settings: Settings
    clock: Clock
    database: Database
    signals: Signals
    tracing: Tracing
    registry: CollectorRegistry
    authentication: Authentication


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Member:
    """A package with a schema, assembled: the platform, a module or a channel."""

    name: str
    schema: str
    events: EventsSection
    schedules: tuple[ScheduleDecl, ...]
    consumers: tuple[Consumer, ...]
    kinds: Mapping[str, tuple[JobDecl, JobHandler]]
    routers: tuple[APIRouter, ...]
    """What the app's role mounts of the package's routers."""
    checks: Mapping[str, Check]
    resources: tuple[AbstractAsyncContextManager[object], ...]
    migrations: SchemaMigrations


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Handed:
    """What a package's assembly handed over, before it is checked against its manifest."""

    name: str
    schema: str
    events: EventsSection
    jobs: tuple[JobDecl, ...]
    schedules: tuple[ScheduleDecl, ...]
    consumers: Sequence[ConsumerSpec]
    handlers: Sequence[JobSpec]
    routers: Sequence[APIRouter]
    checks: Mapping[str, Check]
    resources: Sequence[AbstractAsyncContextManager[object]]
    migrations: SchemaMigrations | None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Assembled:
    """An app's packages, assembled and checked."""

    modules: tuple[PackageManifest, ...]
    """In assembly order."""
    members: tuple[Member, ...]
    """The platform first, then the modules in assembly order, then the channels."""
    bound: Mapping[str, BoundDatabase]
    schedules: tuple[Schedule, ...]


def _ordered(modules: Iterable[Module]) -> tuple[Module, ...]:
    """``modules`` in dependency order: a module after every module it depends on."""
    by_name = {module.manifest.package.name: module for module in modules}
    graph: dict[str, tuple[str, ...]] = {}
    for name, module in by_name.items():
        graph[name] = module.manifest.dependencies.modules
        for dependency in graph[name]:
            if dependency not in by_name:
                raise ModuleMissingError(name, dependency)
    try:
        order = tuple(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as error:
        raise ModuleCycleError(_CYCLE.validate_python(error.args[1])) from error
    return tuple(by_name[name] for name in order)


def _schema_of(manifest: PackageManifest) -> str:
    """The schema a module owns (``SchemaMissingError`` without one)."""
    schema = manifest.schema_name
    if schema is None:
        raise SchemaMissingError(manifest.package.name)
    return schema


@final
@dataclass(frozen=True, slots=True)
class _Claim:
    """A schema's owner, and what its manifest declares the schema's units may do."""

    package: str
    events: EventsSection
    jobs: tuple[JobDecl, ...]


def _claims(modules: Sequence[Module], packs: Sequence[ChannelPack]) -> dict[str, _Claim]:
    """Each schema and the package that owns it: the platform's, then the modules', then the channels'."""
    claims = {PLATFORM: _Claim(PLATFORM, PLATFORM_MANIFEST.events, PLATFORM_MANIFEST.jobs)}
    wanted = [
        (
            _schema_of(module.manifest),
            _Claim(module.manifest.package.name, module.manifest.events, module.manifest.jobs),
        )
        for module in modules
    ]
    wanted += [
        (pack.entry.schema_name, _Claim(pack.key, pack.entry.events, pack.entry.jobs)) for pack in packs
    ]
    for schema, claim in wanted:
        owner = claims.setdefault(schema, claim)
        if owner is not claim:
            raise DuplicateSchemaError(schema, owner.package, claim.package)
    return claims


def _bound(shared: Shared, schema: str, claim: _Claim) -> BoundDatabase:
    """The units of ``schema``, in its role, for what its owner's manifest declares."""
    return BoundDatabase(
        shared.database,
        schema=schema,
        source=claim.package,
        signals=shared.signals,
        publishes=frozenset(published.type for published in claim.events.publishes),
        jobs={
            decl.kind: JobPolicy(max_attempts=decl.max_attempts, deadline=decl.deadline)
            for decl in claim.jobs
        },
    )


@final
@dataclass(frozen=True, slots=True)
class _Rules:
    """How a declaration and a handler are matched, and what each mismatch raises."""

    label: str
    undeclared: Callable[[str, str], LedgerError]
    not_provided: Callable[[str, str], LedgerError]


_CONSUMERS: Final = _Rules("consumer", ConsumerUndeclaredError, ConsumerNotProvidedError)
_JOBS: Final = _Rules("job kind", JobUndeclaredError, JobNotProvidedError)


def _matched[D, H](
    package: str, declared: Mapping[str, D], handed: Iterable[tuple[str, H]], rules: _Rules
) -> dict[str, tuple[D, H]]:
    """Each declaration with its one handler; ``rules`` say what a mismatch raises."""
    matched: dict[str, tuple[D, H]] = {}
    for name, handler in handed:
        if name in matched:
            raise HandlerDuplicateError(rules.label, name)
        if name not in declared:
            raise rules.undeclared(package, name)
        matched[name] = (declared[name], handler)
    for name in declared:
        if name not in matched:
            raise rules.not_provided(package, name)
    return matched


def _member(handed: Handed) -> Member:
    """``handed`` checked against its manifest (see the module)."""
    migrations = handed.migrations
    if migrations is None:
        raise MigrationsMissingError(handed.name, handed.schema)
    if migrations.schema != handed.schema:
        raise ForeignSchemaError(handed.name, migrations.schema)
    consumers = _matched(
        handed.name,
        {decl.consumer: decl for decl in handed.events.consumes},
        ((spec.name, spec) for spec in handed.consumers),
        _CONSUMERS,
    )
    kinds = _matched(
        handed.name,
        {decl.kind: decl for decl in handed.jobs},
        ((spec.kind, spec.handler) for spec in handed.handlers),
        _JOBS,
    )
    return Member(
        name=handed.name,
        schema=handed.schema,
        events=handed.events,
        schedules=handed.schedules,
        consumers=tuple(Consumer.of(spec, decl) for decl, spec in consumers.values()),
        kinds=kinds,
        routers=tuple(handed.routers),
        checks=handed.checks,
        resources=tuple(handed.resources),
        migrations=migrations,
    )


@final
class _Assembly:
    """One assembly's directories and bound databases (see the module)."""

    __slots__ = ("_apis", "_bound", "_channels", "_extensions", "_ports", "_regimes", "_shared")

    def __init__(self, shared: Shared, regimes: Regimes, bound: Mapping[str, BoundDatabase]) -> None:
        self._shared = shared
        self._regimes = regimes
        self._bound = bound
        self._apis = Directory("api")
        self._extensions = Directory("extension")
        self._ports = Directory("port")
        self._channels = AssembledChannels()

    def module(self, module: Module) -> Handed:
        """Assemble ``module`` with the APIs of its dependencies; bind what it hands over."""
        shared, manifest = self._shared, module.manifest
        name = manifest.package.name
        database = self._bound[_schema_of(manifest)]
        context = ModuleContext(
            manifest=manifest,
            role=shared.role,
            settings=shared.settings,
            database=database,
            clock=shared.clock,
            regimes=self._regimes,
            channels=self._channels,
            apis=api_view(manifest, self._apis),
            extensions=extension_view(manifest, self._extensions),
            telemetry=Telemetry.of(name, shared.tracing, shared.registry),
            auth=shared.authentication,
        )
        parts = module.assemble(context)
        bind_parts(manifest, parts, apis=self._apis, extensions=self._extensions, ports=self._ports)
        mounted = manifest.http is not None and shared.role in manifest.http.mounted_in
        return Handed(
            name=name,
            schema=database.schema,
            events=manifest.events,
            jobs=manifest.jobs,
            schedules=manifest.schedules,
            consumers=parts.consumers,
            handlers=parts.jobs,
            routers=parts.routers if mounted else (),
            checks=parts.checks,
            resources=parts.resources,
            migrations=parts.migrations,
        )

    def require_bindings(self, modules: Sequence[Module]) -> None:
        """Every extension point and port a module declares is bound (``BindingMissingError``)."""
        for module in modules:
            points = module.manifest.extension_points
            for point in points.declares:
                self._extensions.value_of(f"{module.manifest.package.name}.{point}")
            for port in points.provides:
                self._ports.value_of(port)

    def channel(self, pack: ChannelPack, provided: frozenset[str]) -> Handed:
        """Assemble the channel ``pack`` with the ports modules provide; add it to the assembled channels."""
        shared, entry = self._shared, pack.entry
        context = ChannelContext(
            definition=entry,
            regime=self._regimes.get(pack.regime),
            role=shared.role,
            settings=shared.settings,
            database=self._bound[entry.schema_name],
            clock=shared.clock,
            ports=self._ports.view(provided),
            telemetry=Telemetry.of(pack.key, shared.tracing, shared.registry),
        )
        parts = pack.assemble(context)
        self._channels.add(pack.key, parts.channel)
        return Handed(
            name=pack.key,
            schema=entry.schema_name,
            events=entry.events,
            jobs=entry.jobs,
            schedules=entry.schedules,
            consumers=parts.consumers,
            handlers=parts.jobs,
            routers=parts.routers if shared.role == "api" else (),
            checks=parts.checks,
            resources=parts.resources,
            migrations=parts.migrations,
        )

    def seal(self) -> None:
        """Every channel is in: modules read them from now on."""
        self._channels.seal()


def _schedules(members: Sequence[Member], bound: Mapping[str, BoundDatabase]) -> tuple[Schedule, ...]:
    schedules: list[Schedule] = []
    for member in members:
        for decl in member.schedules:
            if decl.every is None:
                raise CronScheduleError(decl.name)
            schedules.append(Schedule(decl.name, decl.every, decl.enqueues, bound[member.schema]))
    return tuple(schedules)


def assemble(shared: Shared, discover: Discover) -> Assembled:
    """Load, assemble and check every package (see the module)."""
    regimes = Regimes.load(discover)
    packs = Channels.load(regimes, discover).packs
    modules = _ordered(load_modules(discover))
    bound = {schema: _bound(shared, schema, claim) for schema, claim in _claims(modules, packs).items()}
    assembly = _Assembly(shared, regimes, bound)
    handed = [assembly.module(module) for module in modules]
    assembly.require_bindings(modules)
    provided = frozenset(port for module in modules for port in module.manifest.extension_points.provides)
    handed += [assembly.channel(pack, provided) for pack in packs]
    assembly.seal()
    platform = Handed(
        name=PLATFORM,
        schema=PLATFORM,
        events=PLATFORM_MANIFEST.events,
        jobs=PLATFORM_MANIFEST.jobs,
        schedules=PLATFORM_MANIFEST.schedules,
        consumers=(),
        handlers=maintenance_jobs(tuple(bound.values())),
        routers=(),
        checks={},
        resources=(),
        migrations=platform_migrations(),
    )
    members = tuple(_member(each) for each in (platform, *handed))
    seen: set[str] = set()
    for consumer in (consumer for member in members for consumer in member.consumers):
        if consumer.name in seen:  # each runs as a worker of its name: one name, one consumer
            raise HandlerDuplicateError(_CONSUMERS.label, consumer.name)
        seen.add(consumer.name)
    return Assembled(
        modules=tuple(module.manifest for module in modules),
        members=members,
        bound=bound,
        schedules=_schedules(members, bound),
    )
