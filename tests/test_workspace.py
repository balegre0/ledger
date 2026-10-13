"""The gate covers every package of the workspace.

A workspace member (``[tool.uv.workspace] members``) left out of one of the lists the gate runs from is a
failure here, not a package silently left unchecked.
"""

import tomllib
from pathlib import Path
from typing import Final, NotRequired, TypedDict

from pydantic import TypeAdapter

ROOT: Final = Path(__file__).resolve().parent.parent
MODULES_DIRECTORY: Final = "modules"
SERVICES_CONTRACT: Final = "Libraries never import services"
BILLING_DEPTRY: Final = "deptry extras/billing/src --config extras/billing/pyproject.toml"


class Task(TypedDict):
    cmd: NotRequired[str]
    sequence: NotRequired[list[str]]


class Poe(TypedDict):
    tasks: dict[str, Task]


class CoverageRun(TypedDict):
    source_pkgs: list[str]
    source: NotRequired[list[str]]


class Coverage(TypedDict):
    run: CoverageRun


class Contract(TypedDict):
    name: str
    forbidden_modules: NotRequired[list[str]]


class ImportLinter(TypedDict):
    root_packages: list[str]
    contracts: list[Contract]


class Pytest(TypedDict):
    testpaths: list[str]


class UvWorkspace(TypedDict):
    members: list[str]


class Uv(TypedDict):
    workspace: UvWorkspace


class Tools(TypedDict):
    uv: Uv
    poe: Poe
    coverage: Coverage
    importlinter: ImportLinter
    pytest: Pytest


class Workspace(TypedDict):
    tool: Tools


WORKSPACE: Final = TypeAdapter(Workspace)


def packages(root: Path, config: Workspace) -> dict[str, tuple[str, ...]]:
    """Every workspace member by directory (``packages/kernel``), with the modules its ``src`` holds."""
    found: dict[str, tuple[str, ...]] = {}
    for member in config["tool"]["uv"]["workspace"]["members"]:
        for project in sorted(root.glob(f"{member}/pyproject.toml")):
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
        if directory.startswith(f"{MODULES_DIRECTORY}/"):
            shielded = {
                module
                for contract in tools["importlinter"]["contracts"]
                if contract["name"] == SERVICES_CONTRACT
                for module in contract.get("forbidden_modules", [])
            }
            missing.extend(
                f"{directory}: {module} not in the forbidden modules of '{SERVICES_CONTRACT}'"
                for module in modules
                if module not in shielded
            )
        # A package with a member nested inside it (countries/do holds countries/do/testing) names its own
        # src and tests: the nested member's are its own task's, and their `tests` would collide in one run.
        checked = {f"mypy {directory}", f"mypy {directory}/src {directory}/tests"}
        mypy = {name for name, command in commands.items() if command in checked}
        if not mypy & in_sequence["mypy"]:
            missing.append(f"{directory}: no task of the mypy sequence runs 'mypy {directory}'")
        deptry = f"deptry {directory}/src --config {directory}/pyproject.toml"
        if not {name for name, command in commands.items() if command == deptry} & in_sequence["deps"]:
            missing.append(f"{directory}: no task of the deps sequence runs '{deptry}'")
        if not any(directory.startswith(path) for path in tools["pytest"]["testpaths"]):
            missing.append(f"{directory}: under no [tool.pytest] testpaths")
    return missing


def unmeasured_migrations(root: Path, config: Workspace) -> list[str]:
    """Every migrations directory of a service that coverage is not told of by path.

    Alembic runs a migration environment and its scripts as modules named after their files, outside the
    service's package: coverage, which picks ``source_pkgs`` by module name, measures them only through a
    ``source`` entry, which it matches by path.
    """
    measured = set(config["tool"]["coverage"]["run"].get("source", []))
    return [
        f"{directory}: not in [tool.coverage.run] source"
        for directory in sorted(
            found.relative_to(root).as_posix()
            for found in (root / MODULES_DIRECTORY).glob("*/src/**/migrations")
            if found.is_dir()
        )
        if directory not in measured
    ]


def workspace() -> Workspace:
    return WORKSPACE.validate_python(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")))


def test_every_package_of_the_workspace_is_in_every_list_the_gate_runs_from() -> None:
    config = workspace()
    found = packages(ROOT, config)
    assert {"packages/kernel", "packages/platform", "packages/testing", "modules/identity"} <= found.keys()
    assert unguarded(found, config) == []


def test_a_package_left_out_of_the_lists_is_named() -> None:
    """The check itself: a service the configuration does not know of is reported in every list."""
    config = workspace()
    found = packages(ROOT, config) | {"extras/billing": ("ledger_billing",)}
    assert unguarded(found, config) == [
        "extras/billing: ledger_billing not in [tool.coverage.run] source_pkgs",
        "extras/billing: ledger_billing not in [tool.importlinter] root_packages",
        "extras/billing: no task of the mypy sequence runs 'mypy extras/billing'",
        f"extras/billing: no task of the deps sequence runs '{BILLING_DEPTRY}'",
        "extras/billing: under no [tool.pytest] testpaths",
    ]


def test_a_service_the_libraries_may_import_is_named() -> None:
    config = workspace()
    found = packages(ROOT, config) | {"modules/billing": ("ledger_billing",)}
    assert [line for line in unguarded(found, config) if "Libraries never" in line] == [
        "modules/billing: ledger_billing not in the forbidden modules of 'Libraries never import services'"
    ]


def test_every_services_migrations_are_measured_by_coverage() -> None:
    assert unmeasured_migrations(ROOT, workspace()) == []


def test_migrations_coverage_is_not_told_of_are_named(tmp_path: Path) -> None:
    (tmp_path / "modules/billing/src/ledger_billing/adapters/migrations").mkdir(parents=True)
    (tmp_path / "modules/billing/src/ledger_billing/migrations.py").touch()  # a module, not a directory
    assert unmeasured_migrations(tmp_path, workspace()) == [
        "modules/billing/src/ledger_billing/adapters/migrations: not in [tool.coverage.run] source"
    ]
