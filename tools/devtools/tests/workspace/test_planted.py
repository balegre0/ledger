"""Adding a package touches only its directory and generated files (ADR 0009), on a copy of the repository.

A new module, and a new country with its dev-only testing package, are planted in a copy of what devtools
reads (the packages, the deploy files, the root's configuration); ``sync`` then writes what they imply and
``check`` finds the copy clean. Every file that changed outside the planted directories is one ``sync``
generates, and of the root ``pyproject.toml`` only its generated block changed.
"""

import shutil
from pathlib import Path
from typing import Final

import pytest

from ledger_devtools.cli import main
from ledger_devtools.generate import targets
from ledger_devtools.root import BEGIN, END
from ledger_devtools.workspace import Workspace

ROOT: Final = Path(__file__).resolve().parents[4]
COPIED: Final = ("packages", "modules", "countries", "apps", "tools", "deploy")
ROOT_FILES: Final = ("pyproject.toml", ".importlinter", ".dockerignore")
LEFT_OUT: Final = shutil.ignore_patterns(
    "tests", "__pycache__", ".*cache*", ".env*", ".dgii", "*.p12", "*.pfx", ".local", "node_modules"
)
"""Tests, caches and every local secret stay behind: devtools reads none of them."""
PLANTED: Final = ("modules/books", "countries/zz")
BOOKS: Final = """[package]
name = "books"
kind = "module"
summary = "A module planted by a test: ledgers of account."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
python = ["sqlalchemy>=2.1.4"]

[data]
schema = "books"

[[ids]]
prefix = "bk"
resource = "book"

[[jobs]]
kind = "books.close"

[[schedules]]
name = "books.close"
every = "24h"
enqueues = "books.close"
"""
COUNTRY: Final = """[package]
name = "zz"
kind = "country"
summary = "A country planted by a test."
owners = ["@o"]

[dependencies]
packages = ["kernel"]
test = ["hypothesis>=6.168.5"]

[country]
iso = "ZZ"
name = "Planted"
currencies = ["USD"]
time_zone = "UTC"
authority = { name = "Planted authority", short = "PA", url = "https://example.invalid" }

[[regimes]]
key = "zz"
entry_point = "ledger_country_zz.regime:REGIME"
"""
COUNTRY_TESTING: Final = """[package]
name = "zz-testing"
kind = "package"
summary = "The planted country's dev-only tools."
owners = ["@o"]

[dependencies]
packages = ["kernel", "zz"]
"""
SOURCES: Final = {
    "modules/books/src/ledger_books": ("assembly", "adapters", "application", "domain"),
    "countries/zz/src/ledger_country_zz": ("regime",),
    "countries/zz/testing/src/ledger_country_zz_testing": (),
}


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """What devtools reads of the repository, copied."""
    for directory in COPIED:
        shutil.copytree(ROOT / directory, tmp_path / directory, ignore=LEFT_OUT)
    for name in ROOT_FILES:
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


def snapshot(root: Path) -> dict[Path, str]:
    """Every file of ``root`` outside the planted directories, by path, as text."""
    return {
        path.relative_to(root): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".toml", ".py", ".json", ".sql", ".yaml", ""}
        and not any(path.relative_to(root).as_posix().startswith(f"{planted}/") for planted in PLANTED)
    }


def plant(root: Path) -> None:
    (root / "modules/books").mkdir()
    (root / "modules/books/manifest.toml").write_text(BOOKS, encoding="utf-8", newline="\n")
    (root / "countries/zz/testing").mkdir(parents=True)
    (root / "countries/zz/manifest.toml").write_text(COUNTRY, encoding="utf-8", newline="\n")
    (root / "countries/zz/testing/manifest.toml").write_text(COUNTRY_TESTING, encoding="utf-8", newline="\n")
    for source, children in SOURCES.items():
        (root / source).mkdir(parents=True)
        (root / source / "__init__.py").write_text('"""Planted."""\n', encoding="utf-8", newline="\n")
        for child in children:
            (root / source / child).mkdir()
            (root / source / child / "__init__.py").write_text('"""Planted."""\n', encoding="utf-8")


def outside_the_block(text: str) -> tuple[str, str]:
    head, _, rest = text.partition(f"{BEGIN}\n")
    return head, rest.partition(f"{END}\n")[2]


def test_a_planted_module_and_country_need_no_hand_edit_outside_their_directories(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    before = snapshot(repository)
    plant(repository)
    assert main(["sync", "--root", str(repository)]) == 0
    capsys.readouterr()
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    after = snapshot(repository)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    generated = set(targets(Workspace.load(repository)))
    assert changed <= generated
    assert {Path("pyproject.toml"), Path(".importlinter"), Path("deploy/local/postgres/init.sql")} <= changed
    assert outside_the_block(after[Path("pyproject.toml")]) == outside_the_block(
        before[Path("pyproject.toml")]
    )
    root = after[Path("pyproject.toml")]
    assert "ledger-books = { workspace = true }\n" in root
    assert '    "ledger-country-zz-testing",\n' in root  # the workspace group: a dev-only package
    assert '    "ledger_country_zz",\n' in root  # known-first-party
