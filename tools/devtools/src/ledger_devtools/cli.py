"""``ledger-devtools``: ``sync`` writes what the manifests imply, ``check`` proves nothing is stale.

Both work in two phases. The OpenAPI snapshots are made from the installed ``api`` app, which imports every
module and, with it, files ``sync`` generates (a module's ``_manifest.py``, identity's role table): made in
the process that also finds those stale, they would come from the stale ones, or the app would not even
assemble. So every other file is written (or found current) first, and the snapshots are made after, in a
fresh interpreter (a spawned process), which imports what is on disk now.
"""

import argparse
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import ClassVar, Final, final
lazy from collections.abc import Mapping, Sequence

from ledger_devtools.checks import check
from ledger_devtools.generate import mounted, openapi_targets, orphans, static_targets
from ledger_devtools.openapi import NotInstalledError, api_document, require_installed
from ledger_devtools.protos import proto_problems
from ledger_devtools.repository import repository_problems
from ledger_devtools.workspace import Workspace, WorkspaceError
from ledger_kernel.errors import LedgerError

_PROBLEMS: Final = 1
_UNREADABLE: Final = 2
_ORPHAN: Final = (
    "generated, but no manifest or contract implies it any more: run `uv run ledger-devtools sync`"
)


class SnapshotsUnavailableError(RuntimeError):
    """The process that makes the OpenAPI snapshots failed; ``failure`` is what it said of it."""

    def __init__(self, failure: str) -> None:
        super().__init__(f"the OpenAPI snapshots could not be made: {failure}")


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


def snapshots_in(root: str) -> tuple[dict[str, str], str | None]:
    """In a process of its own: each module's ``openapi.json`` by path, or why it could not be made."""
    try:
        workspace = Workspace.load(Path(root))
        documents = openapi_targets(workspace, api_document)
    except LedgerError as error:  # the parent reports it: nothing of the child's state crosses but this text
        return {}, f"{type(error).__qualname__}: {error}"
    return {path.as_posix(): text for path, text in documents.items()}, None


def openapi_documents(root: Path) -> dict[Path, str]:
    """Each module's ``openapi.json``, made in a fresh interpreter from the packages as on disk now."""
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        documents, failure = pool.submit(snapshots_in, str(root)).result()
    if failure is not None:
        raise SnapshotsUnavailableError(failure)
    return {Path(path): text for path, text in documents.items()}


def _write(root: Path, generated: Mapping[Path, str], stale: Sequence[Path]) -> None:
    for path in stale:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(generated[path], encoding="utf-8", newline="\n")
        sys.stdout.write(f"wrote {path.as_posix()}\n")


def main(arguments: Sequence[str] | None = None) -> int:
    """Run ``sync`` or ``check`` on ``--root`` (the current directory by default); the exit status.

    ``check`` exits 1 when a manifest breaks a rule, a ``.proto`` is not the contract of a module (layout,
    module, package), a generated file is stale, a generated file is one no manifest or contract implies any
    more, or the repository around the manifests disagrees with them (the root's lists, the image pins);
    ``sync`` writes the stale files and deletes those, but writes nothing while a manifest or a contract
    breaks a rule (what it writes follows from them) and exits 1. Both exit 2 when the
    tree has no readable manifest, a module the ``api`` app mounts is not installed, or the snapshots could
    not be made.
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
    problems = [
        f"{problem.where}: {problem.message}" for problem in (*check(workspace), *proto_problems(workspace))
    ]
    if problems:
        return _report(problems)
    try:
        require_installed(mounted(workspace))
    except NotInstalledError as error:
        sys.stderr.write(f"{error}\n")
        return _UNREADABLE
    try:
        return _run(command, root, workspace)
    except SnapshotsUnavailableError as error:
        sys.stderr.write(f"{error}\n")
        return _UNREADABLE


def _run(command: str, root: Path, workspace: Workspace) -> int:
    """``command`` on a workspace whose manifests hold (see ``main``)."""
    generated = static_targets(workspace)
    stale = _stale(root, generated)
    unwanted = orphans(workspace, generated)
    if command == "check":
        # The snapshots are made from current files only: while any other is stale, sync comes first.
        documents = {} if stale or unwanted else openapi_documents(root)
        stale += _stale(root, documents)
        return _report(
            [
                *(f"{path.as_posix()}: stale, run `uv run ledger-devtools sync`" for path in stale),
                *(f"{path.as_posix()}: {_ORPHAN}" for path in unwanted),
                *(f"{problem.where}: {problem.message}" for problem in repository_problems(workspace)),
            ]
        )
    _write(root, generated, stale)
    for path in unwanted:
        (root / path).unlink()
        sys.stdout.write(f"deleted {path.as_posix()}\n")
    documents = openapi_documents(root)
    _write(root, documents, _stale(root, documents))
    return 0
