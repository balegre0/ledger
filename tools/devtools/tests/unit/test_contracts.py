"""The generated ``.importlinter`` proven by behaviour: ``lint-imports`` on a synced tree.

Every contract holds on a tree whose imports are all allowed, and each one breaks on one forbidden import.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from ledger_devtools.cli import main

type Writer = Callable[[str, str, tuple[str, ...]], Path]

TESTING: Final = """
[package]
name = "testing"
kind = "package"
summary = "Test helpers."
owners = ["@o"]
"""
BILLING: Final = """
[package]
name = "billing"
kind = "module"
summary = "Bills."
owners = ["@o"]

[dependencies]
modules = ["identity"]
"""
DO: Final = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@o"]

[dependencies]
packages = ["kernel"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "DGII", short = "DGII", url = "https://dgii.gov.do" }
"""
MX: Final = DO.replace('name = "do"', 'name = "mx"').replace('iso = "DO"', 'iso = "MX"')
DO_TESTING: Final = """
[package]
name = "do-testing"
kind = "package"
summary = "A fake DGII."
owners = ["@o"]

[dependencies]
packages = ["do"]
"""
PLATFORM_CHILDREN: Final = (
    "runtime",
    "http",
    "grpc",
    "events",
    "jobs",
    "auth",
    "data",
    "observability",
    "config",
)
ECF_V1: Final = "countries/do/src/ledger_country_do/channels/ecf_v1"
ALLOWED: Final = {
    "packages/kernel/src/ledger_kernel/__init__.py": "import json",
    "packages/platform/src/ledger_platform/__init__.py": "import ledger_kernel",
    "packages/platform/src/ledger_platform/runtime/__init__.py": "import ledger_platform.config",
    "packages/platform/src/ledger_platform/data/__init__.py": "import sqlalchemy",
    "packages/testing/src/ledger_testing/__init__.py": "import pytest",
    "modules/identity/src/ledger_identity/adapters/__init__.py": (
        "import fastapi\nimport ledger_identity.application"
    ),
    "modules/identity/src/ledger_identity/application/__init__.py": "import ledger_platform.data",
    "modules/identity/src/ledger_identity/domain/__init__.py": (
        "import ledger_kernel\nimport ledger_identity.events"
    ),
    "modules/billing/src/ledger_billing/__init__.py": "import ledger_identity",
    "countries/do/src/ledger_country_do/regime/__init__.py": "import ledger_kernel",
    "countries/do/testing/src/ledger_country_do_testing/__init__.py": "import ledger_country_do",
    "apps/api/src/ledger_app_api/main.py": "import ledger_platform.runtime",
    f"{ECF_V1}/adapters/__init__.py": "import lxml\nimport ledger_country_do.channels.ecf_v1.application",
    f"{ECF_V1}/application/__init__.py": "import ledger_country_do.channels.ecf_v1.domain",
    f"{ECF_V1}/domain/__init__.py": "import ledger_kernel\nimport ledger_country_do.regime",
}
FORBIDDEN: Final = {
    "workspace-layers": ("packages/kernel/src/ledger_kernel/__init__.py", "import ledger_platform"),
    "kernel-is-stdlib-only": ("packages/kernel/src/ledger_kernel/__init__.py", "import sqlalchemy"),
    "platform-layers": (
        "packages/platform/src/ledger_platform/config/__init__.py",
        "import ledger_platform.runtime",
    ),
    "do-regime-is-pure": (
        "countries/do/src/ledger_country_do/regime/__init__.py",
        "import ledger_country_do.channels",
    ),
    "mx-regime-is-pure": ("countries/mx/src/ledger_country_mx/regime/__init__.py", "import ledger_platform"),
    "modules-never-import-countries": (
        "modules/identity/src/ledger_identity/__init__.py",
        "import ledger_country_do",
    ),
    "countries-never-import-modules": (
        "countries/do/src/ledger_country_do/__init__.py",
        "import ledger_identity",
    ),
    "countries-are-independent": (
        "countries/do/src/ledger_country_do/__init__.py",
        "import ledger_country_mx",
    ),
    "identity-is-protected": (
        "modules/billing/src/ledger_billing/__init__.py",
        "import ledger_identity.domain",
    ),
    "billing-is-protected": (
        "packages/platform/src/ledger_platform/data/__init__.py",
        "import ledger_billing._manifest",
    ),
    "identity-declared-edges": ("modules/identity/src/ledger_identity/__init__.py", "import ledger_billing"),
    "identity-layers": (
        "modules/identity/src/ledger_identity/domain/__init__.py",
        "import ledger_identity.application",
    ),
    "billing-layers": ("modules/billing/src/ledger_billing/extra.py", "import ledger_kernel"),
    "identity-domain-is-pure": (
        "modules/identity/src/ledger_identity/events/__init__.py",
        "import ledger_platform",
    ),
    "identity-application-is-pure": (
        "modules/identity/src/ledger_identity/application/__init__.py",
        "import ledger_country_mx",
    ),
    "production-never-imports-test-tooling": (
        "packages/platform/src/ledger_platform/__init__.py",
        "import ledger_testing",
    ),
    "apps-only-compose": ("apps/api/src/ledger_app_api/main.py", "import ledger_platform.data"),
    "api-declared-edges": ("apps/api/src/ledger_app_api/main.py", "import ledger_kernel"),
    "billing-declared-edges": ("modules/billing/src/ledger_billing/__init__.py", "import ledger_testing"),
    "do-declared-edges": ("countries/do/src/ledger_country_do/__init__.py", "import ledger_testing"),
    "do-testing-declared-edges": (
        "countries/do/testing/src/ledger_country_do_testing/__init__.py",
        "import ledger_platform",
    ),
    "kernel-declared-edges": ("packages/kernel/src/ledger_kernel/__init__.py", "import ledger_testing"),
    "mx-declared-edges": ("countries/mx/src/ledger_country_mx/__init__.py", "import ledger_testing"),
    "platform-declared-edges": (
        "packages/platform/src/ledger_platform/__init__.py",
        "import ledger_identity",
    ),
    "testing-declared-edges": ("packages/testing/src/ledger_testing/__init__.py", "import ledger_identity"),
    "do-channel-ecf_v1-layers": (
        f"{ECF_V1}/domain/__init__.py",
        "import ledger_country_do.channels.ecf_v1.adapters",
    ),
    "do-channel-ecf_v1-is-pure": (f"{ECF_V1}/domain/__init__.py", "import sqlalchemy"),
    "only-the-chassis-proves-tokens": (
        "modules/identity/src/ledger_identity/adapters/__init__.py",
        "import ledger_platform.http.proving",
    ),
}
_CONTRACT: Final = re.compile(r"^\[importlinter:contract:(.+)\]$", re.MULTILINE)


def _add(root: Path, path: str, line: str) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8") if target.is_file() else '"""Module."""\n'
    target.write_text(f"{text}{line}\n", encoding="utf-8")


@pytest.fixture
def synced(tree: Path, write: Writer) -> Path:
    """Every kind of package, every contract §4.6 generates, imports that are all allowed; then ``sync``."""
    write("packages/testing", TESTING, ())
    write("modules/billing", BILLING, ())
    write("countries/do", DO, ("regime", "channels"))
    write("countries/mx", MX, ("regime",))
    write("countries/do/testing", DO_TESTING, ())
    for child in PLATFORM_CHILDREN:
        directory = tree / "packages/platform/src/ledger_platform" / child
        directory.mkdir()
        (directory / "__init__.py").write_text('"""Layer."""\n', encoding="utf-8")
    (tree / "packages/platform/src/ledger_platform/http/proving.py").write_text(
        '"""The chassis caller from a token alone."""\n', encoding="utf-8"
    )
    for layer in ("adapters", "application", "domain"):
        directory = tree / ECF_V1 / layer
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text('"""Layer."""\n', encoding="utf-8")
    (tree / ECF_V1 / "__init__.py").write_text('"""Channel."""\n', encoding="utf-8")
    (tree / "countries/do/src/ledger_country_do/channels/__init__.py").write_text(
        '"""Channels."""\n', encoding="utf-8"
    )
    for child in ("events",):
        directory = tree / "modules/identity/src/ledger_identity" / child
        directory.mkdir()
        (directory / "__init__.py").write_text('"""Layer."""\n', encoding="utf-8")
    (tree / "modules/identity/src/ledger_identity/assembly.py").write_text(
        '"""Assembly."""\n', encoding="utf-8"
    )
    for path, line in ALLOWED.items():
        _add(tree, path, line)
    assert main(["sync", "--root", str(tree)]) == 0
    return tree


def _lint(root: Path, *contracts: str) -> tuple[int, str]:
    executable = shutil.which("lint-imports")
    assert executable is not None
    sources = sorted(str(source) for source in root.glob("**/src") if source.is_dir())
    done = subprocess.run(
        [executable, "--no-cache", *(argument for name in contracts for argument in ("--contract", name))],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sources)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stdout


def test_every_generated_contract_has_a_forbidden_import(synced: Path) -> None:
    contracts = _CONTRACT.findall((synced / ".importlinter").read_text(encoding="utf-8"))
    assert sorted(contracts) == sorted(FORBIDDEN)


def test_every_contract_holds_when_the_imports_are_allowed(synced: Path) -> None:
    code, output = _lint(synced)
    assert (code, f"Contracts: {len(FORBIDDEN)} kept, 0 broken." in output) == (0, True), output


@pytest.mark.parametrize("contract", sorted(FORBIDDEN))
def test_each_contract_breaks_on_its_forbidden_import(synced: Path, contract: str) -> None:
    path, line = FORBIDDEN[contract]
    _add(synced, path, line)
    code, output = _lint(synced, contract)
    assert (code, "Contracts: 0 kept, 1 broken." in output) == (1, True), output
