"""The workspace as its manifests describe it: every package, found by glob (§4.1, ADR 0009)."""

import tomllib
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import ClassVar, Final, Self, final
lazy from collections.abc import Mapping
lazy from pathlib import Path

from ledger_devtools.tree import Scan, scan
from ledger_kernel.errors import LedgerError
from ledger_platform.config.manifest import InvalidManifestError, PackageKind, load_manifest
from ledger_platform.config.names import PLATFORM
lazy from ledger_platform.config.manifest import PackageManifest

MANIFEST_GLOBS: Final = (
    "packages/*/manifest.toml",
    "modules/*/manifest.toml",
    "countries/*/manifest.toml",
    "countries/*/testing/manifest.toml",
    "apps/*/manifest.toml",
    "tools/*/manifest.toml",
)
DEV_ONLY: Final = frozenset({"ledger-testing", "ledger-devtools"})
"""Distributions never shipped in the image (with every ``*-testing`` one)."""
MODULE_LAYERS: Final = ("assembly", "adapters", "application", "api", "domain", "events", "_manifest")
"""What a module's import package may hold, top to bottom (§4.5)."""
CUSTODY: Final = "adapters.custody"
"""What a module isolated in a role holds key material in: only the module and the apps of its role reach it
(§4.6 contract 12, ADR 0010)."""
HTTP_ADAPTER: Final = "adapters.http"
"""Where a module's routes live: an isolated module's forward secrets to its role, never opening them."""
KEY_LIBRARY: Final = "cryptography"
"""What opens key material (PKCS#12, keys): no isolated module's HTTP adapter may reach it."""
_NESTED_DEPTH: Final = 3
"""``countries/<cc>/testing``."""


class WorkspaceError(LedgerError):
    """The workspace cannot be read: no manifest, or manifests the model refuses."""

    code: ClassVar[str] = "workspace_unreadable"

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("; ".join(problems))


@final
@dataclass(frozen=True, slots=True)
class Package:
    """A workspace member: its directory (relative to the root), its manifest, the TOML it was read from."""

    directory: Path
    manifest: PackageManifest
    raw: Mapping[str, object] = field(compare=False)

    @property
    def name(self) -> str:
        """``package.name``."""
        return self.manifest.package.name

    @property
    def kind(self) -> PackageKind:
        """``package.kind``."""
        return self.manifest.package.kind

    @property
    def manifest_path(self) -> str:
        """Where the manifest is, relative to the root: what a problem is reported at."""
        return f"{self.directory.as_posix()}/manifest.toml"

    @property
    def nested_country(self) -> str | None:
        """The country code of a ``countries/<cc>/testing`` package (Decision 27), else ``None``."""
        parts = self.directory.parts
        nested = len(parts) == _NESTED_DEPTH and parts[0] == "countries" and parts[2] == "testing"
        return parts[1] if nested else None

    @property
    def distribution(self) -> str:
        """The distribution: from the manifest, or ``ledger-country-<cc>-testing`` for a nested package."""
        country = self.nested_country
        return self.manifest.distribution if country is None else f"ledger-country-{country}-testing"

    @property
    def import_name(self) -> str:
        """The one top-level import package of the distribution."""
        return self.distribution.replace("-", "_")

    @property
    def source(self) -> Path:
        """``<directory>/src/<import package>``."""
        return self.directory / "src" / self.import_name

    @property
    def is_production(self) -> bool:
        """Whether the distribution ships in the image."""
        return self.distribution not in DEV_ONLY and not self.distribution.endswith("-testing")


@final
@dataclass(frozen=True, slots=True)
class Workspace:
    """Every package of the repository at ``root``, sorted by distribution."""

    root: Path
    packages: tuple[Package, ...]
    seen: Scan = field(default=Scan((), ()), compare=False)
    """What git would see under ``root`` (``ledger_devtools.tree``): all devtools ever reads or deletes."""

    @classmethod
    def load(cls, root: Path) -> Self:
        """Read every manifest under ``root``; every problem is reported at once, with its path."""
        seen = scan(root)
        found = [root / path for path in seen.files if any(path.full_match(glob) for glob in MANIFEST_GLOBS)]
        if not found:
            raise WorkspaceError((f"no manifest.toml under {root}",))
        packages: list[Package] = []
        problems: list[str] = []
        for path in found:
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            try:
                manifest = load_manifest(text)
            except InvalidManifestError as error:
                problems.append(f"{relative}: {error}")
                continue
            packages.append(Package(path.parent.relative_to(root), manifest, tomllib.loads(text)))
        if problems:
            raise WorkspaceError(tuple(problems))
        ordered = sorted(packages, key=lambda package: (package.distribution, package.manifest_path))
        return cls(root, tuple(ordered), seen)

    @property
    def named(self) -> tuple[Package, ...]:
        """The packages a manifest can name (every kind but apps), in distribution order.

        A manifest names what it depends on by name; an app is depended on by nothing, so its name is in a
        namespace of its own: an app may be named like the module it runs (``apps/signer`` beside
        ``modules/signer``). Whatever is keyed per package is keyed by distribution, which no two share.
        """
        return tuple(package for package in self.packages if package.kind is not PackageKind.APP)

    def by_name(self, name: str) -> Package:
        """The package a manifest means by ``name``, never an app (``KeyError`` if none)."""
        return self._find(self.named, name)

    def app(self, name: str) -> Package:
        """The app named ``name`` (``KeyError`` if none)."""
        return self._find(self.apps, name)

    @staticmethod
    def _find(packages: tuple[Package, ...], name: str) -> Package:
        for package in packages:
            if package.name == name:
                return package
        raise KeyError(name)

    def of_kind(self, kind: PackageKind) -> tuple[Package, ...]:
        """The packages of ``kind``, in distribution order."""
        return tuple(package for package in self.packages if package.kind is kind)

    @property
    def modules(self) -> tuple[Package, ...]:
        """Every module, in distribution order."""
        return self.of_kind(PackageKind.MODULE)

    @property
    def countries(self) -> tuple[Package, ...]:
        """Every country, in distribution order."""
        return self.of_kind(PackageKind.COUNTRY)

    @property
    def apps(self) -> tuple[Package, ...]:
        """Every app, in distribution order."""
        return self.of_kind(PackageKind.APP)

    def module_graph(self) -> dict[str, tuple[str, ...]]:
        """Each module's name and the modules it depends on or consumes from, by name."""
        names = {package.name for package in self.modules}
        return {
            package.name: tuple(edge for edge in module_edges(self, package) if edge in names)
            for package in self.modules
        }

    def module_order(self) -> tuple[Package, ...]:
        """Modules, each after the modules it depends on or consumes from (``check`` proves it is a DAG)."""
        order = TopologicalSorter(self.module_graph()).static_order()
        return tuple(self.by_name(name) for name in order)

    def children(self, package: Package) -> tuple[str, ...]:
        """The top-level submodules and subpackages of ``package``'s import package on disk, sorted."""
        return self.entries(package.source)

    def entries(self, directory: Path) -> tuple[str, ...]:
        """The modules and packages git sees directly in ``directory`` (relative to the root), sorted."""
        packages = {path.name for path in self.seen.directories if path.parent == directory}
        modules = {
            path.stem
            for path in self.seen.files
            if path.parent == directory and path.suffix == ".py" and path.name != "__init__.py"
        }
        return tuple(sorted((packages - {"__pycache__"}) | modules))

    def matching(self, pattern: str) -> tuple[Path, ...]:
        """The visible files whose path relative to the root matches ``pattern``."""
        return tuple(path for path in self.seen.files if path.full_match(pattern))

    def schemas(self) -> tuple[str, ...]:
        """Every schema in migration order: the platform's, then each module's, then each channel's."""
        platform = tuple(package for package in self.named if package.name == PLATFORM)
        names: list[str] = []
        for package in (*platform, *self.module_order(), *self.countries):
            if package.manifest.schema_name is not None:
                names.append(package.manifest.schema_name)
            names.extend(channel.schema_name for channel in package.manifest.channels)
        return tuple(dict.fromkeys(names))


def module_edges(workspace: Workspace, package: Package) -> tuple[str, ...]:
    """What ``package`` depends on: the modules it declares and the packages whose events it consumes."""
    producers = {
        event.type: other.name
        for other in workspace.packages
        for event in (
            *other.manifest.events.publishes,
            *(published for channel in other.manifest.channels for published in channel.events.publishes),
        )
    }
    consumed = (
        producers[event_type]
        for consumer in package.manifest.events.consumes
        for event_type in consumer.types
        if event_type in producers
    )
    edges = dict.fromkeys((*package.manifest.dependencies.modules, *consumed))
    return tuple(name for name in edges if name != package.name)
