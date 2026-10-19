"""The composition seam (§3.10): what a module or a channel is given to assemble, and what it hands back.

The app gives each module a ``ModuleContext`` and each channel a ``ChannelContext``; their ``assemble``
returns ``ModuleParts`` or ``ChannelParts``, which the app merges and mounts by its role. A context holds the
package's own settings section and units of its own schema: never the app's settings, its pool or another
package's secrets. Assembly only
builds: nothing is started, opened or called until every package has assembled. The channels a module reads
(``AssembledChannels``) are filled after channel assembly, so a module reads them at call time, never while
it assembles.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager

import structlog
lazy from fastapi import APIRouter
lazy from opentelemetry.trace import Tracer
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import LedgerError
from ledger_platform.runtime.facade import require_facades
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.regimes import RegimeDef
lazy from ledger_platform.config.manifest import AppRole, ChannelEntry, PackageManifest
lazy from ledger_platform.config.settings import PackageSettings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_platform.events.consumers import ConsumerSpec
lazy from ledger_platform.http.authentication import Authentication
lazy from ledger_platform.jobs.outcomes import JobSpec
lazy from ledger_platform.observability.health import Check
lazy from ledger_platform.observability.tracing import Tracing
lazy from ledger_platform.runtime.keys import (
    ApiDirectory,
    Binding,
    ChainDirectory,
    Directory,
    ExtensionDirectory,
    Ports,
)
lazy from ledger_platform.runtime.registries import Regimes

NO_CHECKS: Final[Mapping[str, Check]] = MappingProxyType({})


class ChannelsNotAssembledError(LedgerError):
    """The channels were read before the app had assembled them: a module read them while it assembled."""

    code: ClassVar[str] = "channels_not_assembled"

    def __init__(self) -> None:
        super().__init__("the channels are read at call time, once the app has assembled them")


class ChannelsSealedError(LedgerError):
    """A channel added, or the channels sealed again, after the app sealed them: a defect of the app."""

    code: ClassVar[str] = "channels_sealed"

    def __init__(self) -> None:
        super().__init__("the channels were sealed already")


class DuplicateChannelError(LedgerError):
    """Two channels of one key: a defect of the app's assembly."""

    code: ClassVar[str] = "channel_duplicate"

    def __init__(self, key: str) -> None:
        super().__init__(f"channel {key!r} is added twice")


class SectionMissingError(LedgerError):
    """A package that reads a settings section it does not have (or with another model than its own)."""

    code: ClassVar[str] = "section_missing"

    def __init__(self, package: str, model: str) -> None:
        super().__init__(
            f"{package!r} has no settings section read by {model}: declare it, hand that model over"
        )


def _section[S: PackageSettings](package: str, settings: PackageSettings | None, model: type[S]) -> S:
    if not isinstance(settings, model):
        raise SectionMissingError(package, model.__qualname__)
    return settings


class ChannelUnknownError(LedgerError):
    """A channel key no installed pack assembled."""

    code: ClassVar[str] = "channel_unknown"

    def __init__(self, key: str) -> None:
        super().__init__(f"no channel {key!r} is assembled")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Telemetry:
    """A package's logger, tracer and metrics registry."""

    logger: structlog.stdlib.BoundLogger
    """Bound to ``package=<name>``."""
    tracer: Tracer
    """Named after the package."""
    registry: CollectorRegistry
    """The app's registry: a package's metrics are exported with the app's."""

    @classmethod
    def of(cls, name: str, tracing: Tracing, registry: CollectorRegistry) -> Telemetry:
        """Telemetry scoped to the package ``name``."""
        return cls(
            logger=structlog.stdlib.get_logger(name).bind(package=name),
            tracer=tracing.provider.get_tracer(name),
            registry=registry,
        )


@final
class AssembledChannels:
    """The assembled channels by key: filled and sealed by the app, read by modules at call time."""

    __slots__ = ("_channels", "_sealed")

    def __init__(self) -> None:
        self._channels: dict[str, object] = {}
        self._sealed = False

    def add(self, key: str, channel: object) -> None:
        """The app adds each assembled channel once, before ``seal``."""
        if self._sealed:
            raise ChannelsSealedError
        if key in self._channels:
            raise DuplicateChannelError(key)
        self._channels[key] = channel

    def seal(self) -> None:
        """Every channel is in: from now on they are read, never added."""
        if self._sealed:
            raise ChannelsSealedError
        self._sealed = True

    def get(self, key: str) -> object:
        """The channel of ``key`` (fiscal-kit narrows it to its ``Channel`` Protocol)."""
        if not self._sealed:
            raise ChannelsNotAssembledError
        if key not in self._channels:
            raise ChannelUnknownError(key)
        return self._channels[key]

    def keys(self) -> tuple[str, ...]:
        """The assembled channels' keys, sorted."""
        if not self._sealed:
            raise ChannelsNotAssembledError
        return tuple(sorted(self._channels))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleContext:
    """What a module is given to assemble (§3.10)."""

    manifest: PackageManifest
    """The module's manifest (its generated ``_manifest.py``)."""
    role: AppRole
    """The app assembling it: what it mounts depends on it."""
    settings: PackageSettings | None
    """Its own settings section (``LEDGER_<MODULE>_*``), validated; ``None`` if its manifest declares none."""
    database: BoundDatabase
    """Units in the module's role and schema only: no role argument exists."""
    clock: Clock
    regimes: Regimes
    """Every installed regime, validated and frozen."""
    channels: AssembledChannels
    """Filled after channel assembly: read at call time, never during ``assemble``."""
    channel_entries: tuple[ChannelEntry, ...] = ()
    """The manifest entries of every installed channel, by key: data, known while the module assembles."""
    apis: ApiDirectory
    """Other modules' APIs: only those of declared dependencies."""
    extensions: ExtensionDirectory
    """Implementations of the extension points the module declares, bound by the app."""
    telemetry: Telemetry
    """Logger, tracer and metrics scoped to the module."""
    auth: Authentication
    """The app's request authentication: its routers declare ``Depends(context.auth.caller)``."""

    def section[S: PackageSettings](self, model: type[S]) -> S:
        """Its settings section, read by ``model`` (``SectionMissingError`` if it has none of that model)."""
        return _section(self.manifest.package.name, self.settings, model)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleParts:
    """What a module's assembly hands the app (§3.10); schedules come from the manifest only (Decision 15)."""

    api: Binding | None = None
    """The module's API implementation, bound under its key (for later modules)."""
    implements: tuple[Binding, ...] = ()
    """Implementations of other modules' extension points."""
    provides: tuple[Binding, ...] = ()
    """Ports provided to channels (a signer, a company directory)."""
    routers: Sequence[APIRouter] = ()
    """Mounted by ``api``."""
    consumers: Sequence[ConsumerSpec] = ()
    """A handler for each consumer the manifest declares."""
    jobs: Sequence[JobSpec] = ()
    """A handler for each job kind the manifest declares."""
    checks: Mapping[str, Check] = NO_CHECKS
    """Readiness checks by name."""
    resources: Sequence[AbstractAsyncContextManager[object]] = ()
    """Opened at start, closed at stop."""
    migrations: SchemaMigrations | None = None
    """The schema's revisions, used by ``migrate``."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Module:
    """What the entry point group ``ledger.modules`` names: a manifest and its assembly (Decision 13)."""

    manifest: PackageManifest
    assemble: Callable[[ModuleContext], ModuleParts]
    settings: type[PackageSettings] | None = None
    """The model of its settings section, if its manifest declares one (``[package] settings``)."""
    migrations: SchemaMigrations | None = None
    """Its schema's revisions, for what migrates it without assembling it: the suites of the modules that
    depend on it (``ledger_testing.modules``). The app takes them from what its assembly hands over."""


def bind_parts(
    manifest: PackageManifest,
    parts: ModuleParts,
    *,
    apis: Directory,
    extensions: ChainDirectory,
    ports: Directory,
) -> None:
    """Bind what a module's assembly hands over, with the authority its manifest gives it.

    Its API only under its own name; extension point implementations only under the qualified names it
    declares (``[extension_points] implements``, as ``<declaring module>.<Protocol>``); ports only under the
    names it declares (``provides``). What other packages call (an API, an extension point implementation,
    a port) must have every public coroutine method a ``@facade``.
    """
    name = manifest.package.name
    if parts.api is not None:
        require_facades(parts.api.value)
        apis.add(parts.api, by=name, declared=frozenset({name}))
    implements = frozenset(manifest.extension_points.implements)
    for binding in parts.implements:
        require_facades(binding.value)
        extensions.add(
            binding,
            by=name,
            declared=implements,
            order=manifest.extension_points.order_of(binding.name),
        )
    provides = frozenset(manifest.extension_points.provides)
    for binding in parts.provides:
        require_facades(binding.value)
        ports.add(binding, by=name, declared=provides)


def api_view(manifest: PackageManifest, apis: Directory) -> ApiDirectory:
    """The APIs a module may read: those of the modules it depends on."""
    return apis.view(frozenset(manifest.dependencies.modules))


def extension_view(manifest: PackageManifest, extensions: ChainDirectory) -> ExtensionDirectory:
    """The implementations a module may read: of the extension points it declares, by qualified name."""
    name = manifest.package.name
    return extensions.view(frozenset(f"{name}.{point}" for point in manifest.extension_points.declares))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelContext:
    """What a channel is given to assemble (§3.10; Decision 11)."""

    definition: ChannelEntry
    """The channel's manifest entry."""
    regime: RegimeDef
    """The regime it serves."""
    role: AppRole
    settings: PackageSettings | None
    """Its own settings section (``LEDGER_<SCHEMA>_*``), validated; ``None`` if its entry declares none."""
    database: BoundDatabase
    """Units in the channel's own role and schema."""
    clock: Clock
    ports: Ports
    """What modules provide to channels; fiscal-kit defines the keys."""
    telemetry: Telemetry

    def section[S: PackageSettings](self, model: type[S]) -> S:
        """Its settings section, read by ``model`` (``SectionMissingError`` if it has none of that model)."""
        return _section(self.definition.key, self.settings, model)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelParts:
    """What a channel's assembly hands the app (§3.10)."""

    channel: object
    """The implementation fiscal routes to (fiscal-kit's ``Channel`` Protocol)."""
    routers: Sequence[APIRouter] = ()
    """Public routes, mounted by ``api``."""
    receiver_routers: Sequence[APIRouter] = ()
    """Mounted by ``receiver``."""
    consumers: Sequence[ConsumerSpec] = ()
    jobs: Sequence[JobSpec] = ()
    checks: Mapping[str, Check] = NO_CHECKS
    resources: Sequence[AbstractAsyncContextManager[object]] = ()
    migrations: SchemaMigrations | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelPack:
    """What the entry point group ``ledger.countries.channels`` names: a channel and its assembly.

    Like a ``Module`` (Decision 13): the manifest entry (from the country's ``_manifest.py``), the regime it
    serves and the function that assembles it.
    """

    entry: ChannelEntry
    regime: str
    """The key of the regime the channel serves."""
    assemble: Callable[[ChannelContext], ChannelParts]
    settings: type[PackageSettings] | None = None
    """The model of its settings section, if its entry declares one (``settings = true``)."""

    @property
    def key(self) -> str:
        """``<cc>-<name>-v<n>``: the manifest entry's key, and the entry point's name."""
        return self.entry.key
