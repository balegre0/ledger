"""The gate covers every package of the workspace.

A package added under ``libs/`` or ``services/`` and left out of one of the lists the gate runs from is a
failure here, not a package silently left unchecked.
"""

import tomllib
from pathlib import Path
from typing import Final, NotRequired, TypedDict

from pydantic import TypeAdapter

ROOT: Final = Path(__file__).resolve().parent.parent
PACKAGE_DIRECTORIES: Final = ("libs", "services")
BILLING_DEPTRY: Final = "deptry services/billing/src --config services/billing/pyproject.toml"


class Task(TypedDict):
    cmd: NotRequired[str]
    sequence: NotRequired[list[str]]


class Poe(TypedDict):
    tasks: dict[str, Task]


class CoverageRun(TypedDict):
    source_pkgs: list[str]


class Coverage(TypedDict):
    run: CoverageRun


class ImportLinter(TypedDict):
    root_packages: list[str]


class Pytest(TypedDict):
    testpaths: list[str]


class Tools(TypedDict):
    poe: Poe
    coverage: Coverage
    importlinter: ImportLinter
    pytest: Pytest


class Workspace(TypedDict):
    tool: Tools


WORKSPACE: Final = TypeAdapter(Workspace)


def packages(root: Path) -> dict[str, tuple[str, ...]]:
    """Every package of the workspace by directory (``libs/kernel``), with the modules its ``src`` holds."""
    found: dict[str, tuple[str, ...]] = {}
    for parent in PACKAGE_DIRECTORIES:
        for project in sorted((root / parent).glob("*/pyproject.toml")):
            directory = project.parent
            modules = tuple(sorted(init.parent.name for init in (directory / "src").glob("*/__init__.py")))
            found[directory.relative_to(root).as_posix()] = modules
    return found


def unguarded(packages: dict[str, tuple[str, ...]], config: Workspace) -> list[str]:
    """What the gate would leave out: one line per package and list it is missing from."""
    tools = config["tool"]
    tasks = tools["poe"]["tasks"]
    commands = {name: task.get("cmd", "") for name, task in tasks.items()}
    in_sequence = {
        name: set(tasks.get(sequence, Task()).get("sequence", []))
        for name, sequence in (("mypy", "mypy"), ("deps", "deps"))
    }
    missing: list[str] = []
    for directory, modules in packages.items():
        for module in modules:
            if module not in tools["coverage"]["run"]["source_pkgs"]:
                missing.append(f"{directory}: {module} not in [tool.coverage.run] source_pkgs")
            if module not in tools["importlinter"]["root_packages"]:
                missing.append(f"{directory}: {module} not in [tool.importlinter] root_packages")
        mypy = {name for name, command in commands.items() if command == f"mypy {directory}"}
        if not mypy & in_sequence["mypy"]:
            missing.append(f"{directory}: no task of the mypy sequence runs 'mypy {directory}'")
        deptry = f"deptry {directory}/src --config {directory}/pyproject.toml"
        if not {name for name, command in commands.items() if command == deptry} & in_sequence["deps"]:
            missing.append(f"{directory}: no task of the deps sequence runs '{deptry}'")
        if not any(directory.startswith(path) for path in tools["pytest"]["testpaths"]):
            missing.append(f"{directory}: under no [tool.pytest] testpaths")
    return missing


def workspace() -> Workspace:
    return WORKSPACE.validate_python(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")))


def test_every_package_of_the_workspace_is_in_every_list_the_gate_runs_from() -> None:
    found = packages(ROOT)
    assert {"libs/kernel", "libs/contracts", "libs/platform"} <= found.keys()
    assert unguarded(found, workspace()) == []


def test_a_package_left_out_of_the_lists_is_named() -> None:
    """The check itself: a service the configuration does not know of is reported in every list."""
    found = packages(ROOT) | {"services/billing": ("ledger_billing",)}
    assert unguarded(found, workspace()) == [
        "services/billing: ledger_billing not in [tool.coverage.run] source_pkgs",
        "services/billing: ledger_billing not in [tool.importlinter] root_packages",
        "services/billing: no task of the mypy sequence runs 'mypy services/billing'",
        f"services/billing: no task of the deps sequence runs '{BILLING_DEPTRY}'",
        "services/billing: under no [tool.pytest] testpaths",
    ]
