"""Adding a package touches only its directory and generated files (ADR 0009), on a copy of the repository.

A new module, and a new country with its dev-only testing package, are planted in a copy of what devtools
reads (the packages, the deploy files, the root's configuration); ``sync`` then writes what they imply and
``check`` finds the copy clean. Every file that changed outside the planted directories is one ``sync``
generates, and of the root ``pyproject.toml`` only its generated block changed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final
lazy from collections.abc import Mapping

lazy import pytest

from ledger_devtools.cli import main
from ledger_devtools.generate import targets
from ledger_devtools.root import BEGIN, END
from ledger_devtools.tree import visible_files
from ledger_devtools.workspace import Workspace

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


def snapshot(root: Path) -> dict[Path, str]:
    """Every file of ``root`` git would see, outside the planted directories, by path, as text."""
    return {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in visible_files(root)
        if relative.suffix in {".toml", ".py", ".json", ".sql", ".yaml", ""}
        and not any(relative.as_posix().startswith(f"{planted}/") for planted in PLANTED)
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


VAULT: Final = """[package]
name = "vault"
kind = "module"
summary = "A module planted by a test: keys, isolated in the signer's role."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[data]
schema = "vault"
isolated_in = "signer"
"""


def test_a_planted_isolated_module_gets_the_signer_login_and_a_custody_contract(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = without_the_signer(repository) / "modules/vault/src/ledger_vault"
    (source / "adapters").mkdir(parents=True)
    (repository / "modules/vault/manifest.toml").write_text(VAULT, encoding="utf-8", newline="\n")
    for package in (source, source / "adapters"):
        (package / "__init__.py").write_text('"""Planted."""\n', encoding="utf-8", newline="\n")
    assert main(["sync", "--root", str(repository)]) == 0
    capsys.readouterr()
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    init_sql = (repository / "deploy/local/postgres/init.sql").read_text(encoding="utf-8")
    assert 'GRANT "vault_rw" TO "ledger_signer"' in init_sql
    assert 'GRANT "vault_rw" TO "ledger_app"' not in init_sql
    contracts = (repository / ".importlinter").read_text(encoding="utf-8")
    assert "[importlinter:contract:vault-custody]\n" in contracts
    assert "forbidden_modules =\n    ledger_vault.adapters.custody\n" in contracts


def test_a_role_isolates_one_module_at_most(repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A second module isolated in the signer's role fails sync and check, naming both (rule of two)."""
    for name in ("vault", "lockbox"):
        manifest = VAULT.replace('"vault"', f'"{name}"')
        write_files(
            repository,
            {
                f"modules/{name}/manifest.toml": manifest,
                f"modules/{name}/src/ledger_{name}/__init__.py": PLANTED_DOCSTRING,
            },
        )
    problem = (
        "modules 'lockbox' and 'vault' are both isolated in the signer's role: a role isolates one module"
    )
    for command in ("sync", "check"):
        assert main([command, "--root", str(repository)]) == 1
        assert problem in capsys.readouterr().out


PLANTED_DOCSTRING: Final = '"""Planted."""\n'
CUSTODY_KEY: Final = f"{PLANTED_DOCSTRING}\nfrom ledger_signer.adapters.custody.keys import KEY\n"
"""Code importing a key of the signer's custody."""
PLANTED_FILES: Final = {
    "modules/signer/src/ledger_signer/adapters/custody/keys.py": f'{PLANTED_DOCSTRING}\nKEY = "k"\n',
}
"""A key in the workspace's own signer custody, which its app (``apps/signer``, named like the module: see
``test_an_app_named_like_a_module_is_a_package_of_its_own``) reaches through the runtime alone."""
LEAKS: Final = {
    "apps/api/src/ledger_app_api/leak.py": "import ledger_signer.adapters.custody.keys\n",
    "packages/testing/src/ledger_testing/leak.py": "from ledger_signer.adapters.custody import keys\n",
    "modules/identity/src/ledger_identity/adapters/leak.py": CUSTODY_KEY,
    "modules/signer/src/ledger_signer/__init__.py": CUSTODY_KEY,
    "modules/invoicing/src/ledger_invoicing/adapters/reexported.py": "from ledger_signer import KEY\n",
}
"""An app of another role, a test kit and a module importing the custody; its re-export, imported."""


def without_the_signer(root: Path) -> Path:
    """The copy without the workspace's own isolated module (and its contract): a role isolates one module, so
    a planted ``vault`` stands in for it."""
    for directory in ("modules/signer", "contracts/proto/ledger/signer"):
        shutil.rmtree(root / directory)
    return root


def write_files(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8", newline="\n")


CUSTODY_CONTRACT: Final = (
    "Nothing but signer and the apps of the signer role reaches signer's custody, even indirectly"
)
APPS_CONTRACT: Final = "The apps of the signer role reach signer's custody through its assembly only"


def lint_imports(root: Path) -> tuple[int, str]:
    """import-linter over ``root`` (its packages found before the installed ones): exit status, words."""
    sources = [*sorted(str(path) for path in root.glob("*/*/src")), str(root / "countries/do/testing/src")]
    process = subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports_command; lint_imports_command()"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sources)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return process.returncode, " ".join((process.stdout + process.stderr).split())  # it wraps long names


def test_import_linter_breaks_on_anything_but_the_role_s_apps_reaching_the_custody(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_files(repository, PLANTED_FILES)
    assert main(["sync", "--root", str(repository)]) == 0
    capsys.readouterr()
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    status, report = lint_imports(repository)
    assert status == 0, report
    assert f"{CUSTODY_CONTRACT} KEPT" in report
    assert f"{APPS_CONTRACT} KEPT" in report
    write_files(repository, LEAKS)
    status, report = lint_imports(repository)
    assert status != 0
    assert f"{CUSTODY_CONTRACT} BROKEN" in report
    assert f"{APPS_CONTRACT} KEPT" in report
    broken = report.split("Broken contracts", 1)[1].split(CUSTODY_CONTRACT, 1)[1].lstrip(" -")
    section = broken.split(" ---", 1)[0]
    for importer in ("ledger_app_api.leak", "ledger_testing.leak", "ledger_identity.adapters.leak"):
        assert f"{importer} -> ledger_signer.adapters.custody" in section, section
    assert "ledger_invoicing.adapters.reexported -> ledger_signer " in section, section
    assert main(["check", "--root", str(repository)]) == 1
    assert "ledger_signer/__init__.py loads ledger_signer.adapters.custody" in capsys.readouterr().out


NAMESAKE_APP: Final = """[package]
name = "identity"
kind = "app"
summary = "An app planted by a test: named like the module the others depend on."
owners = ["@o"]

[dependencies]
packages = ["platform"]

[app]
script = "ledger-identity"
role = "worker"
"""
NAMESAKE_FILES: Final = {
    "apps/identity/manifest.toml": NAMESAKE_APP,
    "apps/identity/src/ledger_app_identity/__init__.py": PLANTED_DOCSTRING,
    "apps/identity/src/ledger_app_identity/main.py": f"{PLANTED_DOCSTRING}\nimport ledger_platform.runtime\n",
}
"""An app named like a module (as ``apps/signer`` is named like ``modules/signer``): a name a manifest
depends on names the module, never the app, and every contract and table keyed per package keeps both."""


def test_an_app_named_like_a_module_is_a_package_of_its_own(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_files(repository, NAMESAKE_FILES)
    assert main(["sync", "--root", str(repository)]) == 0
    capsys.readouterr()
    assert main(["check", "--root", str(repository)]) == 0, capsys.readouterr().out
    for dependent in ("modules/signer", "modules/invoicing"):
        pyproject = (repository / dependent / "pyproject.toml").read_text(encoding="utf-8")
        assert '    "ledger-identity",\n' in pyproject
        assert "ledger-app-identity" not in pyproject
    contracts = (repository / ".importlinter").read_text(encoding="utf-8")
    sections = [line for line in contracts.splitlines() if line.startswith("[importlinter:contract:")]
    assert len(sections) == len(set(sections))
    assert "[importlinter:contract:ledger-app-identity-declared-edges]" in sections
    assert "[importlinter:contract:ledger-identity-declared-edges]" in sections
    layers = contracts.split("[importlinter:contract:workspace-layers]", 1)[1].split("\n\n", 1)[0]
    rows = [row.strip().split(" | ") for row in layers.split("layers =\n", 1)[1].splitlines()]
    assert [row for row in rows if "ledger_app_identity" in row] == [rows[0]]
    assert ["ledger_identity"] in rows
    status, report = lint_imports(repository)
    assert status == 0, report


BILLS: Final = """[package]
name = "bills"
kind = "module"
summary = "A module planted by a test: bills, served by the api."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[data]
schema = "bills"

[http]
mounted_in = ["api"]
prefixes = ["/v1/bills"]
"""


def test_a_new_mounted_module_syncs_before_it_is_installed_and_asks_for_the_install(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its pyproject is what installs it: sync writes it and every other file, then asks for the install."""
    write_files(
        repository,
        {
            "modules/bills/manifest.toml": BILLS,
            "modules/bills/src/ledger_bills/__init__.py": PLANTED_DOCSTRING,
        },
    )
    assert main(["sync", "--root", str(repository)]) == 2
    first = capsys.readouterr()
    assert "wrote modules/bills/pyproject.toml" in first.out.splitlines()
    assert "wrote modules/bills/src/ledger_bills/_manifest.py" in first.out.splitlines()
    assert first.err == (
        "modules/bills/manifest.toml: ledger-bills is not installed: installed packages are behind: "
        "run `uv sync --all-packages`, then sync again\n"
    )
    assert main(["sync", "--root", str(repository)]) == 2  # what it could write is written
    assert capsys.readouterr().out == ""
    assert main(["check", "--root", str(repository)]) == 2
