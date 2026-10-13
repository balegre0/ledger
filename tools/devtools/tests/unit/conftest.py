"""A small workspace written into a temporary directory: the fixture every devtools test reads."""

from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Final

import pytest

KERNEL: Final = """
[package]
name = "kernel"
kind = "package"
summary = "Value types."
owners = ["@o"]
"""
PLATFORM: Final = """
[package]
name = "platform"
kind = "package"
summary = "The chassis."
owners = ["@o"]
scripts = { "ledger-probe" = "ledger_platform.observability.probe:main" }

[dependencies]
packages = ["kernel"]
python = ["sqlalchemy[asyncio]>=2.1.4", "prometheus-client>=0.26.0"]

[data]
schema = "platform"

[[jobs]]
kind = "platform.purge_done_jobs"

[[schedules]]
name = "platform.purge_done_jobs"
every = "24h"
enqueues = "platform.purge_done_jobs"
"""
IDENTITY: Final = """
[package]
name = "identity"
kind = "module"
summary = "Organizations and companies."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
python = ["fastapi>=0.143"]

[data]
schema = "identity"

[[events.publishes]]
type = "identity.company.registered"
version = 1

[http]
mounted_in = ["api"]
prefixes = ["/v1/companies"]
"""
API: Final = """
[package]
name = "api"
kind = "app"
summary = "The API app."
owners = ["@o"]

[dependencies]
packages = ["platform"]
python = ["granian>=2.8.4"]

[app]
script = "ledger-api"
role = "api"
"""
_PREFIXES: Final = {"app": "ledger_app_", "country": "ledger_country_"}
PACKAGE_DOCSTRING: Final = '"""Package."""\n'

type Writer = Callable[[str, str, tuple[str, ...]], Path]
"""``write(directory, manifest, children)``: a package and its import package, returned."""
type Editor = Callable[[str, str, str], None]
"""``edit(directory, old, new)``: ``old`` replaced in that manifest (the test fails if it is not there)."""


def _write(root: Path, directory: str, manifest: str, children: tuple[str, ...]) -> Path:
    package = root / directory
    package.mkdir(parents=True)
    (package / "manifest.toml").write_text(dedent(manifest), encoding="utf-8")
    name = manifest.split('name = "', 1)[1].split('"', 1)[0]
    kind = manifest.split('kind = "', 1)[1].split('"', 1)[0]
    parts = directory.split("/")
    nested = len(parts) == 3 and parts[0] == "countries" and parts[2] == "testing"
    source = f"ledger_country_{parts[1]}_testing" if nested else f"{_PREFIXES.get(kind, 'ledger_')}{name}"
    imported = package / "src" / source.replace("-", "_")
    imported.mkdir(parents=True)
    (imported / "__init__.py").write_text(PACKAGE_DOCSTRING, encoding="utf-8")
    for child in children:
        (imported / child).mkdir()
        (imported / child / "__init__.py").write_text(PACKAGE_DOCSTRING, encoding="utf-8")
    return imported


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Kernel, platform, the identity module and the api app, each coherent with the others."""
    _write(tmp_path, "packages/kernel", KERNEL, ())
    _write(tmp_path, "packages/platform", PLATFORM, ())
    _write(tmp_path, "modules/identity", IDENTITY, ("adapters", "application", "domain"))
    _write(tmp_path, "apps/api", API, ())
    return tmp_path


@pytest.fixture
def write(tree: Path) -> Writer:
    """Add a package to ``tree``."""

    def writer(directory: str, manifest: str, children: tuple[str, ...]) -> Path:
        return _write(tree, directory, manifest, children)

    return writer


@pytest.fixture
def edit(tree: Path) -> Editor:
    """Replace text in one of ``tree``'s manifests."""

    def editor(directory: str, old: str, new: str) -> None:
        path = tree / directory / "manifest.toml"
        text = path.read_text(encoding="utf-8")
        assert old in text
        path.write_text(text.replace(old, new), encoding="utf-8")

    return editor
