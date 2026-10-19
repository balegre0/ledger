"""The manifest rules that need the whole workspace (§4.7 ``check``): one function per rule.

A single manifest is validated by the platform's model when the workspace is loaded; what is checked here is
how manifests agree: names and directories, unique values, dependencies that exist, an acyclic module graph,
consumers with a producer, schedules with a job kind, modules made of layers only, countries named by their
directories, settings sections apart.
"""

from dataclasses import dataclass
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Final, final
lazy from collections.abc import Callable, Iterator, Mapping

from pydantic import TypeAdapter

from ledger_devtools.workspace import MODULE_LAYERS
from ledger_kernel.access import Role
from ledger_platform.config.manifest import PackageKind
from ledger_platform.config.settings import Settings, section_prefix
lazy from ledger_devtools.workspace import Package, Workspace
lazy from ledger_platform.config.manifest import (
    ChannelEntry,
    EventsSection,
    IdPrefix,
    JobDecl,
    PermissionDecl,
    ScheduleDecl,
)

_DEPENDED_ON: Final = frozenset({PackageKind.PACKAGE, PackageKind.COUNTRY})
"""What ``dependencies.packages`` may name: modules go in ``dependencies.modules``; apps and tools are not."""
_CYCLE: Final = TypeAdapter(tuple[str, ...])


@final
@dataclass(frozen=True, slots=True)
class Problem:
    """A rule a manifest breaks: the manifest's path (relative to the root) and what is wrong."""

    where: str
    message: str


@final
@dataclass(frozen=True, slots=True)
class _Scope:
    """What a package, or one of its channels, declares; ``owner`` starts its consumers, jobs, schedules."""

    package: Package
    owner: str
    schema: str | None
    ids: tuple[IdPrefix, ...]
    permissions: tuple[PermissionDecl, ...]
    events: EventsSection
    jobs: tuple[JobDecl, ...]
    schedules: tuple[ScheduleDecl, ...]
    http_prefixes: tuple[str, ...]

    def problem(self, message: str) -> Problem:
        """``message``, reported at the package's manifest."""
        return Problem(self.package.manifest_path, message)


def _channel_scope(package: Package, channel: ChannelEntry) -> _Scope:
    return _Scope(
        package=package,
        owner=channel.schema_name,
        schema=channel.schema_name,
        ids=channel.ids,
        permissions=channel.permissions,
        events=channel.events,
        jobs=channel.jobs,
        schedules=channel.schedules,
        http_prefixes=channel.http_prefixes,
    )


def _scopes(workspace: Workspace) -> Iterator[_Scope]:
    for package in workspace.packages:
        manifest = package.manifest
        yield _Scope(
            package=package,
            owner=package.name.replace("-", "_"),
            schema=manifest.schema_name,
            ids=manifest.ids,
            permissions=manifest.permissions,
            events=manifest.events,
            jobs=manifest.jobs,
            schedules=manifest.schedules,
            http_prefixes=() if manifest.http is None else manifest.http.prefixes,
        )
        yield from (_channel_scope(package, channel) for channel in manifest.channels)


class _Unique(StrEnum):
    """A value one package at most may declare; the value of each member is how a duplicate is phrased."""

    SCHEMA = "schema '{}' is owned by {} too"
    ID_PREFIX = "id prefix '{}' is minted by {} too"
    PERMISSION = "permission '{}' is declared by {} too"
    EVENT_TYPE = "event type '{}' is published by {} too"
    CONSUMER = "consumer '{}' is declared by {} too"
    JOB_KIND = "job kind '{}' is declared by {} too"
    SCHEDULE = "schedule '{}' is declared by {} too"
    HTTP_PREFIX = "http prefix '{}' is owned by {} too"


def _values(scope: _Scope) -> Mapping[_Unique, tuple[str, ...]]:
    return {
        _Unique.SCHEMA: () if scope.schema is None else (scope.schema,),
        _Unique.ID_PREFIX: tuple(declared.prefix for declared in scope.ids),
        _Unique.PERMISSION: tuple(declared.code for declared in scope.permissions),
        _Unique.EVENT_TYPE: tuple(declared.type for declared in scope.events.publishes),
        _Unique.CONSUMER: tuple(declared.consumer for declared in scope.events.consumes),
        _Unique.JOB_KIND: tuple(declared.kind for declared in scope.jobs),
        _Unique.SCHEDULE: tuple(declared.name for declared in scope.schedules),
        _Unique.HTTP_PREFIX: scope.http_prefixes,
    }


def _names_are_directories(workspace: Workspace) -> Iterator[Problem]:
    for package in workspace.packages:
        country = package.nested_country
        if country is not None:
            if package.name != f"{country}-testing":
                yield Problem(
                    package.manifest_path, f"package.name '{package.name}' is not '{country}-testing'"
                )
        elif package.name != package.directory.name:
            yield Problem(
                package.manifest_path,
                f"package.name '{package.name}' is not the directory name '{package.directory.name}'",
            )


def _values_are_unique(workspace: Workspace) -> Iterator[Problem]:
    scopes = tuple(_scopes(workspace))
    for unique in _Unique:
        owners: dict[str, str] = {}
        for scope in scopes:
            for value in _values(scope)[unique]:
                if value in owners:
                    yield scope.problem(unique.format(value, owners[value]))
                else:
                    owners[value] = scope.package.name


def _names_start_with_their_owner(workspace: Workspace) -> Iterator[Problem]:
    rule = "does not start with its package's name or channel key"
    for scope in _scopes(workspace):
        named = (
            *(("consumer", declared.consumer) for declared in scope.events.consumes),
            *(("job kind", declared.kind) for declared in scope.jobs),
            *(("schedule", declared.name) for declared in scope.schedules),
        )
        for what, name in named:
            if name.split(".", 1)[0] != scope.owner:
                yield scope.problem(f"{what} '{name}' {rule}")


def _dependency_problem(package: Package, name: str, kinds: Mapping[str, PackageKind]) -> str | None:
    """What is wrong with ``package`` naming ``name`` in ``dependencies.packages``, if anything."""
    kind = kinds.get(name)
    if name == package.name:
        return f"dependencies.packages names '{name}', which is the package itself"
    if kind not in _DEPENDED_ON:
        return f"dependencies.packages names '{name}', which is not a package"
    if kind is PackageKind.COUNTRY and package.kind is not PackageKind.APP and package.nested_country != name:
        return (
            f"dependencies.packages names '{name}', a country, "
            "which only apps and its own testing package may depend on"
        )
    return None


def _dependencies_exist(workspace: Workspace) -> Iterator[Problem]:
    kinds: Mapping[str, PackageKind] = {package.name: package.kind for package in workspace.packages}
    for package in workspace.packages:
        dependencies = package.manifest.dependencies
        for name in dependencies.packages:
            problem = _dependency_problem(package, name, kinds)
            if problem is not None:
                yield Problem(package.manifest_path, problem)
        for name in dependencies.modules:
            if name == package.name:
                yield Problem(
                    package.manifest_path, f"dependencies.modules names '{name}', which is the package itself"
                )
            elif kinds.get(name) is not PackageKind.MODULE:
                yield Problem(
                    package.manifest_path, f"dependencies.modules names '{name}', which is not a module"
                )


def _consumed_types_are_published(workspace: Workspace) -> Iterator[Problem]:
    scopes = tuple(_scopes(workspace))
    published = {event.type for scope in scopes for event in scope.events.publishes}
    for scope in scopes:
        for consumer in scope.events.consumes:
            for event_type in consumer.types:
                if event_type not in published:
                    yield scope.problem(
                        f"consumer '{consumer.consumer}' handles '{event_type}', which no package publishes"
                    )


def _cycle(workspace: Workspace, graph: Mapping[str, tuple[str, ...]], what: str) -> Iterator[Problem]:
    """A cycle of ``graph`` (names to the names they depend on), reported at its first package."""
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as error:
        cycle = _CYCLE.validate_python(error.args[1])
        yield Problem(
            workspace.by_name(cycle[0]).manifest_path, f"the {what} graph has a cycle: {' -> '.join(cycle)}"
        )


def _modules_are_acyclic(workspace: Workspace) -> Iterator[Problem]:
    return _cycle(workspace, workspace.module_graph(), "module")


def _packages_are_acyclic(workspace: Workspace) -> Iterator[Problem]:
    names = {package.name for package in workspace.packages}
    graph = {
        package.name: tuple(
            name for name in package.manifest.dependencies.packages if name in names and name != package.name
        )
        for package in workspace.packages
    }
    return _cycle(workspace, graph, "package")


def _schedules_enqueue_declared_jobs(workspace: Workspace) -> Iterator[Problem]:
    for package in workspace.packages:
        scopes = tuple(scope for scope in _scopes(workspace) if scope.package is package)
        kinds = {job.kind for scope in scopes for job in scope.jobs}
        for schedule in (schedule for scope in scopes for schedule in scope.schedules):
            if schedule.enqueues not in kinds:
                yield Problem(
                    package.manifest_path,
                    (
                        f"schedule '{schedule.name}' enqueues '{schedule.enqueues}', "
                        f"which {package.name} does not declare"
                    ),
                )
            if schedule.cron is not None:
                yield Problem(
                    package.manifest_path,
                    (
                        f"schedule '{schedule.name}' uses cron, "
                        "which is not built until a schedule needs it (§16.4)"
                    ),
                )


def _modules_are_layers(workspace: Workspace) -> Iterator[Problem]:
    layers = ", ".join(MODULE_LAYERS)
    for package in workspace.modules:
        for child in workspace.children(package):
            if child not in MODULE_LAYERS:
                yield Problem(
                    package.manifest_path, f"{package.import_name}.{child} is not a layer ({layers})"
                )


def _countries_are_their_directories(workspace: Workspace) -> Iterator[Problem]:
    """A country's ISO code is its directory's, in capitals; each regime's key starts with that code."""
    for package in workspace.countries:
        country = package.manifest.country
        code = package.directory.name
        if country is not None and country.iso != code.upper():
            yield Problem(
                package.manifest_path,
                f"[country] iso '{country.iso}' is not the directory's code, '{code.upper()}'",
            )
        for regime in package.manifest.regimes:
            if regime.key.split("-", 1)[0] != code:
                rule = "its key starts with the country's code"
                yield Problem(
                    package.manifest_path, f"regime '{regime.key}' is not of country '{code}': {rule}"
                )


def _settings_sections_are_apart(workspace: Workspace) -> Iterator[Problem]:
    """A section's prefix holds no platform variable and no other section's: each variable has one reader."""
    platform = sorted(f"LEDGER_{name.upper()}" for name in Settings.model_fields)
    seen: dict[str, str] = {}
    for package in workspace.packages:
        manifest = package.manifest
        sections = (
            manifest.settings_section,
            *(channel.settings_section for channel in manifest.channels),
        )
        for section in (section for section in sections if section is not None):
            prefix = section_prefix(section)
            named = f"settings section '{section}' ({prefix}*)"
            shadowed = [name for name in platform if name.startswith(prefix)]
            outer = [
                other for other in seen if prefix.startswith(seen[other]) or seen[other].startswith(prefix)
            ]
            if section in seen:
                yield Problem(package.manifest_path, f"{named} is declared twice")
            elif shadowed:
                yield Problem(package.manifest_path, f"{named} would read the platform's {shadowed[0]}")
            elif outer:
                yield Problem(
                    package.manifest_path, f"{named} lies inside section '{outer[0]}' ({seen[outer[0]]}*)"
                )
            seen[section] = prefix


def _the_owner_holds_every_permission(workspace: Workspace) -> Iterator[Problem]:
    """The owner role holds every permission (§7.7): a manifest that does not grant it one is mistaken."""
    for scope in _scopes(workspace):
        for declared in scope.permissions:
            if Role.OWNER not in declared.roles:
                yield scope.problem(
                    f"permission '{declared.code}' is not granted to the owner, who holds every permission"
                )


_RULES: Final[tuple[Callable[[Workspace], Iterator[Problem]], ...]] = (
    _names_are_directories,
    _values_are_unique,
    _the_owner_holds_every_permission,
    _names_start_with_their_owner,
    _dependencies_exist,
    _consumed_types_are_published,
    _modules_are_acyclic,
    _packages_are_acyclic,
    _schedules_enqueue_declared_jobs,
    _modules_are_layers,
    _countries_are_their_directories,
    _settings_sections_are_apart,
)


def check(workspace: Workspace) -> tuple[Problem, ...]:
    """Every problem of every rule, rule by rule, each in distribution order."""
    return tuple(problem for rule in _RULES for problem in rule(workspace))
