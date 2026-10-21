"""The ground devtools' tests grow on, each in a temporary directory.

``tree``: a small, coherent workspace written from scratch (the unit tests). ``repository``: a copy of what
devtools reads of this repository (the workspace tests, which plant packages and contracts in it).
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Final

import pytest

from ledger_devtools.tree import visible_files

ROOT: Final = Path(__file__).resolve().parents[3]
COPIED: Final = ("packages", "modules", "countries", "apps", "tools", "deploy", "contracts")
ROOT_FILES: Final = ("pyproject.toml", ".importlinter", ".dockerignore", ".gitignore")
UNREADABLE: Final = b"TZif2\x00\x00\xff\xfe\x80 not UTF-8"
IGNORED: Final = {
    "deploy/local/.venv/lib/python3.15/site-packages/tzdata/zoneinfo/UTC": UNREADABLE,
    "libs/platform/build/lib/__pycache__/x.cpython-315.pyc": UNREADABLE,
    "tools/build/manifest.toml": UNREADABLE,
    "packages/.venv/manifest.toml": UNREADABLE,
}
"""What a working copy holds besides the repository (a virtual environment, build leftovers), all ignored."""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """What devtools reads of the repository (no test, nothing ignored), then what git would ignore."""
    for relative in visible_files(ROOT):
        if (
            relative.parts[0] in COPIED and "tests" not in relative.parts
        ) or relative.as_posix() in ROOT_FILES:
            (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, tmp_path / relative)
    for ignored, content in IGNORED.items():
        (tmp_path / ignored).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ignored).write_bytes(content)
    return tmp_path


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
