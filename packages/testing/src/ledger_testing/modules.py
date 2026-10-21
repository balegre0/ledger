"""A module's tests, run the way the apps run it (§14.2): what the plugin's module fixtures are made of.

A module's ``conftest.py`` names what it tests and prepares the template with it, in two lines::

    UNDER_TEST = ModuleUnderTest(MANIFEST, MIGRATIONS)


    def pytest_ledger_template(template: Template) -> None:
        UNDER_TEST.prepare(template)

and returns ``UNDER_TEST`` from a ``module_under_test`` fixture. The plugin's fixtures then give its tests the
worker's database migrated as production migrates it (``migrated``, emptied after each test), a pool as the
module's login (``database``: ``ledger_signer`` for a module isolated in the signer's role, else
``ledger_app``) and one as ``ledger_app`` (``app_database``), the module's units in its own role
(``module_database``), a superuser session (``admin``), the apps' settings (``settings``), the ``api`` app
and a client of it (``api``, ``client``), and a fresh, empty database with the schemas
(``fresh_database_name``, ``fresh``, ``fresh_admin_url``). No module copies another's conftest.

A country's suite tests one of its channels the same way: the migrations of the channel's schema, and the
modules that serve its ports (``serving``: a country depends on no module, so its manifest cannot name them);
its app assembles those modules and the country's own channels. No other suite assembles a channel: one needs
ports and a schema that a module's suite does not give it.
"""

import graphlib
from dataclasses import dataclass
from typing import ClassVar, final
lazy from collections.abc import Mapping, Sequence

from pydantic import SecretStr

from ledger_kernel.errors import LedgerError
from ledger_platform.config.manifest import PackageKind
from ledger_platform.config.names import PLATFORM
from ledger_platform.config.settings import Settings
from ledger_platform.data import JobPolicy
from ledger_platform.data.migrations import platform_migrations
from ledger_platform.runtime.packs import Channels, installed_modules, load_modules
from ledger_platform.runtime.registries import CHANNELS, MODULES, Regimes, installed
from ledger_testing.database import bootstrap, migrate
lazy from ledger_platform.config.manifest import ChannelEntry, PackageManifest
lazy from ledger_platform.data.migrations import SchemaMigrations
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_platform.runtime.contexts import Module
lazy from ledger_platform.runtime.registries import Discover, Discovered
lazy from ledger_testing.issuer import LocalIssuer
lazy from ledger_testing.postgres import Template


class NeedUnmetError(LedgerError):
    """A module the one under test depends on is not installed, or its entry point names no revisions."""

    code: ClassVar[str] = "testing_need_unmet"

    def __init__(self, module: str, problem: str) -> None:
        super().__init__(f"{module!r} {problem}: the module under test depends on it")


class SchemaLessModuleError(LedgerError):
    """A package under test that owns no schema, or not the one its migrations are of: nothing to test."""

    code: ClassVar[str] = "testing_module_schema_missing"

    def __init__(self, package: str, schema: str | None = None) -> None:
        if schema is None:
            super().__init__(f"{package!r} owns no schema: its manifest has no [data] schema to test")
        else:
            super().__init__(f"{package!r} owns no schema {schema!r}: neither its [data] nor a channel of it")


@final
@dataclass(frozen=True, slots=True)
class ModuleUnderTest:
    """The package a test suite exercises: its manifest and its schema's revisions."""

    manifest: PackageManifest
    migrations: SchemaMigrations
    discover: Discover = installed
    """Where the modules it depends on are found: the installed entry points, unless a test says otherwise."""
    serving: tuple[str, ...] = ()
    """Modules the app that serves it assembles beside those its manifest depends on: a channel's ports'
    (fiscal's ``CompanyDirectory``, the signer's ``SignerPort``), which a country cannot depend on."""

    def __post_init__(self) -> None:
        if self.manifest.schema_name is None and self._channel() is None:  # a country: one of its channels'
            schema = self.migrations.schema if self.manifest.channels else None
            raise SchemaLessModuleError(self.manifest.package.name, schema)

    def _channel(self) -> ChannelEntry | None:
        """The channel of the manifest whose schema the migrations are of, if they are a channel's."""
        return next(
            (entry for entry in self.manifest.channels if entry.schema_name == self.migrations.schema), None
        )

    @property
    def source(self) -> str:
        """Who the schema's units act for: the package, or the channel that owns the schema (its key)."""
        channel = self._channel()
        return self.manifest.package.name if channel is None else channel.key

    @property
    def schema(self) -> str:
        """The schema the package owns."""
        return self.migrations.schema

    @property
    def needs(self) -> tuple[SchemaMigrations, ...]:
        """The revisions of the modules it depends on: the app that serves it assembles them too.

        Read from the manifests, transitively, each module after those it depends on itself; each is the
        revisions its entry point names (``Module.migrations``). A package that is not a module needs none.
        """
        return tuple(_revisions(module) for module in self._needed())

    @property
    def isolated(self) -> tuple[str, ...]:
        """Those of its ``schemas`` that are the signer login's: of the modules isolated in the signer's role.

        Its own, and those of the modules it needs, by their manifests (``[data] isolated_in``).
        """
        needed = tuple(
            _revisions(module).schema for module in self._needed() if _is_isolated(module.manifest)
        )
        return (*needed, self.schema) if self.is_isolated else needed

    @property
    def is_isolated(self) -> bool:
        """Whether the package is a module isolated in the signer's role: its units log in as the signer."""
        return _is_isolated(self.manifest)

    def _needed(self) -> tuple[Module, ...]:
        """The modules it depends on and those that serve it, transitively, each after those it depends on."""
        pending = [*self.manifest.dependencies.modules, *self.serving]
        if not pending:
            return ()
        modules = {module.manifest.package.name: module for module in installed_modules(self.discover)}
        graph: dict[str, tuple[str, ...]] = {}
        while pending:
            name = pending.pop()
            if name in graph:
                continue
            if name not in modules:
                raise NeedUnmetError(name, "is not installed")
            graph[name] = modules[name].manifest.dependencies.modules
            pending.extend(graph[name])
        order = tuple(graphlib.TopologicalSorter(graph).static_order())
        return tuple(modules[name] for name in order)

    @property
    def schemas(self) -> tuple[str, ...]:
        """The platform's schema, those it needs, then the package's: in migration order."""
        return tuple(dict.fromkeys((PLATFORM, *(need.schema for need in self.needs), self.schema)))

    @property
    def owned(self) -> tuple[str, ...]:
        """The schemas a test empties: the package's, and those it needs (its tests write to them too)."""
        return tuple(dict.fromkeys((self.schema, *(need.schema for need in self.needs))))

    @property
    def publishes(self) -> frozenset[str]:
        """The event types the schema's units may publish: the package's, or its channel's."""
        channel = self._channel()
        events = self.manifest.events if channel is None else channel.events
        return frozenset(event.type for event in events.publishes)

    @property
    def jobs(self) -> Mapping[str, JobPolicy]:
        """The job kinds the schema's units may enqueue, and their limits: the package's or its channel's."""
        channel = self._channel()
        declared = self.manifest.jobs if channel is None else channel.jobs
        return {
            decl.kind: JobPolicy(max_attempts=decl.max_attempts, deadline=decl.deadline) for decl in declared
        }

    def discovering(self) -> Discover:
        """Its ``discover`` as the app that serves its tests finds it: this package and what it needs.

        Every module installed in the workspace is an entry point, so an app that found them all would need
        every schema in every module's test database. A module under test is served with the modules it
        depends on and those that serve it (transitively, by their manifests) and nothing else; a package
        that is not a module, with every module unless modules serve it. Its channels are its own alone (a
        country's: none for a module or the platform); regimes are as installed.
        """
        discover = self.discover
        channels = frozenset(entry.key for entry in self.manifest.channels)
        wanted = self._wanted()

        def only(group: str) -> Sequence[Discovered]:
            found = discover(group)
            if group == CHANNELS:
                return tuple(entry for entry in found if entry.name in channels)
            if group == MODULES and wanted is not None:
                return tuple(entry for entry in found if entry.name in wanted)
            return found

        return only

    def _wanted(self) -> frozenset[str] | None:
        """The modules its app assembles, by name; ``None``: every installed one (see ``discovering``)."""
        is_module = self.manifest.package.kind is PackageKind.MODULE
        if not is_module and not self.serving:
            return None
        manifests = {
            module.manifest.package.name: module.manifest for module in installed_modules(self.discover)
        }
        wanted: set[str] = set()
        pending = [*((self.manifest.package.name,) if is_module else ()), *self.serving]
        while pending:
            name = pending.pop()
            if name not in wanted:
                wanted.add(name)
                needed = manifests[name].dependencies.modules if name in manifests else ()
                pending.extend(needed)
        return frozenset(wanted)

    def prepare(self, template: Template) -> None:
        """The roles and the schemas, each at head as ``ledger_migrator`` with the relay granted."""
        bootstrap(template, self.schemas, isolated=self.isolated)
        revisions: Sequence[SchemaMigrations] = (
            (self.migrations,)
            if self.schema == PLATFORM
            else (platform_migrations(), *self.needs, self.migrations)
        )
        migrate(template, *revisions)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InstalledSchemas:
    """The schemas of a server as ``init.sql`` has them: the platform's, then each installed module's."""

    schemas: tuple[str, ...]
    isolated: tuple[str, ...]
    """Those of modules isolated in the signer's role: ``ledger_signer``'s."""


def installed_schemas(discover: Discover = installed) -> InstalledSchemas:
    """The schemas of the platform, of every module and of every channel ``ledger-migrate`` migrates."""
    manifests = tuple(module.manifest for module in load_modules(discover))
    packs = Channels.load(Regimes.load(discover), discover).packs
    return InstalledSchemas(
        schemas=(
            PLATFORM,
            *(manifest.schema_name for manifest in manifests if manifest.schema_name is not None),
            *(pack.entry.schema_name for pack in packs),
        ),
        isolated=tuple(
            manifest.schema_name
            for manifest in manifests
            if manifest.schema_name is not None and _is_isolated(manifest)
        ),
    )


def _is_isolated(manifest: PackageManifest) -> bool:
    """Whether ``manifest``'s module is isolated in the signer's role: its schema is ``ledger_signer``'s."""
    return manifest.isolated_in == "signer"


def _revisions(module: Module) -> SchemaMigrations:
    """The revisions ``module``'s entry point names (``NeedUnmetError`` without)."""
    if module.migrations is None:
        raise NeedUnmetError(module.manifest.package.name, "names no revisions")
    return module.migrations


@final
class QuietSignals:
    """``Signals`` that tell no one: a module's tests run no worker to wake."""

    __slots__ = ()

    def signal(self, channel: Channel) -> None:
        """Nothing listens."""
        del channel


def app_settings(*, database_url: str, direct_url: str, issuer: LocalIssuer) -> Settings:
    """The apps' settings on a database (units through ``database_url``) and the test's identity provider."""
    return Settings(
        environment="test",
        database_url=SecretStr(database_url),
        database_direct_url=SecretStr(direct_url),
        token_issuer=issuer.issuer,
        token_audience=issuer.audience,
        token_jwks_url=issuer.keys_url,
    )
