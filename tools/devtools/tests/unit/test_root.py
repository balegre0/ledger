"""The root ``pyproject.toml``'s generated block: ``sync`` writes what names the workspace's packages."""

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from ledger_devtools.cli import main
from ledger_devtools.repository import repository_problems
from ledger_devtools.root import BEGIN, END, block_text
from ledger_devtools.workspace import Workspace

type Writer = Callable[[str, str, tuple[str, ...]], Path]

HEAD: Final = """[project]
name = "root"

[dependency-groups]
dev = ["pytest>=9.1.1", { include-group = "workspace" }]
"""
TAIL: Final = """
[tool.ruff]
line-length = 110
"""
TESTING: Final = """
[package]
name = "testing"
kind = "package"
summary = "Test helpers."
owners = ["@o"]

[dependencies]
test = ["hypothesis>=6.168.5"]
"""
COUNTRY_TESTING: Final = """
[package]
name = "do-testing"
kind = "package"
summary = "The pack's dev tools."
owners = ["@o"]

[dependencies]
packages = ["kernel"]
test = ["types-lxml>=2026.2.16", "hypothesis>=6.168.5"]
"""


def root(tree: Path, text: str) -> Path:
    path = tree / "pyproject.toml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def lines(tree: Path) -> list[str]:
    return [f"{problem.where}: {problem.message}" for problem in repository_problems(Workspace.load(tree))]


def test_the_block_names_the_dev_only_distributions_the_test_dependencies_and_every_package(
    tree: Path, write: Writer
) -> None:
    write("packages/testing", TESTING, ())
    write("countries/do/testing", COUNTRY_TESTING, ())
    data = tomllib.loads(f"[dependency-groups]\n{block_text(Workspace.load(tree))}")
    assert data["dependency-groups"] == {
        "workspace": [
            "ledger-country-do-testing",
            "ledger-testing",
            "hypothesis>=6.168.5",
            "types-lxml>=2026.2.16",
        ]
    }
    distributions = [
        "ledger-app-api",
        "ledger-country-do-testing",
        "ledger-identity",
        "ledger-kernel",
        "ledger-platform",
        "ledger-testing",
    ]
    assert data["tool"]["uv"]["sources"] == {name: {"workspace": True} for name in distributions}
    assert data["tool"]["ruff"]["lint"]["isort"] == {
        "known-first-party": [name.replace("-", "_") for name in distributions]
    }


def test_sync_rewrites_only_the_block_and_check_then_finds_it_current(
    tree: Path, write: Writer, capsys: pytest.CaptureFixture[str]
) -> None:
    write("packages/testing", TESTING, ())
    path = root(tree, f"{HEAD}{BEGIN}\nworkspace = []\n{END}\n{TAIL}")
    assert main(["check", "--root", str(tree)]) == 1
    assert "pyproject.toml: stale, run `uv run ledger-devtools sync`" in capsys.readouterr().out.splitlines()
    assert main(["sync", "--root", str(tree)]) == 0
    text = path.read_text(encoding="utf-8")
    assert text == f"{HEAD}{BEGIN}\n{block_text(Workspace.load(tree))}{END}\n{TAIL}"
    assert tomllib.loads(text)["dependency-groups"]["workspace"] == ["ledger-testing", "hypothesis>=6.168.5"]
    capsys.readouterr()
    assert main(["check", "--root", str(tree)]) == 0
    assert capsys.readouterr().out == ""


def test_a_root_without_the_block_is_named_and_left_alone(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = root(tree, HEAD + TAIL)
    assert lines(tree) == [
        (
            f"pyproject.toml: has no generated block: add the lines '{BEGIN}' and '{END}', in that order, "
            "at the end of [dependency-groups]"
        )
    ]
    assert main(["sync", "--root", str(tree)]) == 0
    assert path.read_text(encoding="utf-8") == HEAD + TAIL
    capsys.readouterr()


@pytest.mark.parametrize(
    "text",
    [
        f"{HEAD}{END}\n{BEGIN}\n{TAIL}",
        f"{HEAD}{BEGIN}\n{BEGIN}\n{END}\n{TAIL}",
        f"{HEAD}{BEGIN}\n{TAIL}",
    ],
    ids=["reversed", "twice", "unclosed"],
)
def test_markers_out_of_order_are_no_block(tree: Path, text: str) -> None:
    root(tree, text)
    assert [line.split(":", 2)[1].strip() for line in lines(tree)] == ["has no generated block"]


def test_a_block_outside_the_dependency_groups_is_named(tree: Path) -> None:
    root(tree, f'[dependency-groups]\ndev = [{{ include-group = "workspace" }}]\n{TAIL}{BEGIN}\n{END}\n')
    assert lines(tree) == [
        (
            "pyproject.toml: the generated block must start at the end of [dependency-groups]: "
            "its workspace group is not one there"
        )
    ]


def test_a_dev_group_that_leaves_the_workspace_group_out_is_named(tree: Path) -> None:
    root(tree, f"{HEAD.replace(', { include-group = "workspace" }', '')}{BEGIN}\n{END}\n{TAIL}")
    assert lines(tree) == [
        (
            "pyproject.toml: the dev dependency group does not include the generated one "
            '({ include-group = "workspace" })'
        )
    ]


def test_a_block_that_breaks_the_file_is_named(tree: Path) -> None:
    root(tree, f"{HEAD}{BEGIN}\n{END}\n\n[tool.uv.sources]\nledger-kernel = {{ workspace = true }}\n")
    [line] = lines(tree)
    assert line.startswith("pyproject.toml: the generated block makes the file invalid TOML (line ")
