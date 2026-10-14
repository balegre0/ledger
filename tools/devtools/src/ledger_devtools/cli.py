"""``ledger-devtools``: ``sync`` writes what the manifests imply, ``check`` proves nothing is stale."""

import argparse
import sys
from pathlib import Path
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping, Sequence

from ledger_devtools.checks import check
from ledger_devtools.generate import orphans, targets
from ledger_devtools.openapi import NotInstalledError
from ledger_devtools.repository import repository_problems
from ledger_devtools.workspace import Workspace, WorkspaceError

_PROBLEMS: Final = 1
_UNREADABLE: Final = 2
_ORPHAN: Final = "generated, but no manifest implies it any more: run `uv run ledger-devtools sync`"


@final
class _Arguments(argparse.Namespace):
    """What the command line says, typed (argparse's own namespace is untyped)."""

    commands: ClassVar = ("sync", "check")
    command: str = "check"
    root: Path = Path()


def _stale(root: Path, generated: Mapping[Path, str]) -> list[Path]:
    """The generated files that are missing or differ from what ``sync`` would write."""
    return [
        path
        for path, text in generated.items()
        if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != text
    ]


def _report(lines: Sequence[str]) -> int:
    for line in lines:
        sys.stdout.write(f"{line}\n")
    return _PROBLEMS if lines else 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run ``sync`` or ``check`` on ``--root`` (the current directory by default); the exit status.

    ``check`` exits 1 when a manifest breaks a rule, a generated file is stale, a generated file is one no
    manifest implies any more, or the repository around the manifests disagrees with them (the root's lists,
    the image pins); ``sync`` writes the stale files and deletes those, but writes nothing while
    a manifest breaks a rule (what it writes follows from the manifests) and exits 1. Both exit 2 when the
    tree has no readable manifest, or a module the ``api`` app mounts is not installed.
    """
    parser = argparse.ArgumentParser(prog="ledger-devtools")
    parser.add_argument("command", choices=_Arguments.commands)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parsed = parser.parse_args(arguments, namespace=_Arguments())
    command, root = parsed.command, parsed.root
    try:
        workspace = Workspace.load(root)
    except WorkspaceError as error:
        sys.stderr.write(f"{error}\n")
        return _UNREADABLE
    problems = [f"{problem.where}: {problem.message}" for problem in check(workspace)]
    if problems:
        return _report(problems)
    try:
        generated = targets(workspace)
    except NotInstalledError as error:
        sys.stderr.write(f"{error}\n")
        return _UNREADABLE
    stale = _stale(root, generated)
    unwanted = orphans(workspace, generated)
    if command == "check":
        return _report(
            [
                *(f"{path.as_posix()}: stale, run `uv run ledger-devtools sync`" for path in stale),
                *(f"{path.as_posix()}: {_ORPHAN}" for path in unwanted),
                *(f"{problem.where}: {problem.message}" for problem in repository_problems(workspace)),
            ]
        )
    for path in stale:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(generated[path], encoding="utf-8", newline="\n")
        sys.stdout.write(f"wrote {path.as_posix()}\n")
    for path in unwanted:
        (root / path).unlink()
        sys.stdout.write(f"deleted {path.as_posix()}\n")
    return 0
