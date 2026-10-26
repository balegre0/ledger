"""The repository's files as git sees them: devtools reads nothing ``.gitignore`` leaves out, nor ``.git``.

A working copy holds more than the repository: virtual environments (binary files among them), caches, build
leftovers, local secrets. Every walk of devtools (manifests, generated files it may delete, Dockerfiles,
workspace members, a package's layers on disk) goes through here, so none of that is ever read, reported or
deleted. Each ``.gitignore`` applies under its own directory, the deeper after the shallower, with git's own
semantics (``pathspec``); an ignored directory is never entered. Nor is a nested repository or worktree (a
directory holding a ``.git`` of its own, file or directory): git leaves its contents to it, and another
checkout of this repository (an agent's worktree under ``.claude/worktrees``) would otherwise be read twice.
"""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, final

from pathspec import GitIgnoreSpec

GIT: Final = ".git"
GITIGNORE: Final = ".gitignore"
type _Rules = tuple[tuple[PurePosixPath, GitIgnoreSpec], ...]
"""Each ``.gitignore`` in force and the directory it is in (relative to the root), outermost first."""


@final
@dataclass(frozen=True, slots=True)
class Scan:
    """What git would see under a root (tracked or not, never ignored), relative to it, sorted."""

    files: tuple[Path, ...]
    directories: tuple[Path, ...]
    """Empty ones included: a layer is a directory before it holds a module."""


def _ignored(relative: PurePosixPath, rules: _Rules, *, directory: bool) -> bool:
    """Whether the last ``.gitignore`` that has a say about ``relative`` ignores it.

    ``rules`` are those of ``relative``'s own directory and its parents, so each base is one of its parents.
    """
    verdict = False
    for base, spec in rules:
        inner = relative.relative_to(base).as_posix() + ("/" if directory else "")
        include = spec.check_file(inner).include
        if include is not None:
            verdict = include
    return verdict


def _joined(directory: PurePosixPath, name: str) -> PurePosixPath:
    return directory / name if directory.parts else PurePosixPath(name)


def scan(root: Path) -> Scan:
    """Walk ``root`` as git would see it (see the module)."""
    files: list[Path] = []
    directories: list[Path] = []
    rules_of: dict[Path, _Rules] = {}
    for directory, children, names in os.walk(root):
        here = Path(directory)
        relative = PurePosixPath(here.relative_to(root).as_posix())
        rules = rules_of.get(here.parent, ()) if here != root else ()
        if GITIGNORE in names:
            lines = (here / GITIGNORE).read_text(encoding="utf-8").splitlines()
            rules = (
                *rules,
                (relative, GitIgnoreSpec.from_lines(lines)),
            )
        rules_of[here] = rules
        children[:] = [
            child
            for child in children
            if child != GIT
            and not (here / child / GIT).exists()
            and not _ignored(_joined(relative, child), rules, directory=True)
        ]
        directories.extend(Path(_joined(relative, child)) for child in children)
        files.extend(
            Path(_joined(relative, name))
            for name in names
            if not _ignored(_joined(relative, name), rules, directory=False)
        )
    return Scan(tuple(sorted(files)), tuple(sorted(directories)))


def visible_files(root: Path) -> tuple[Path, ...]:
    """Every file under ``root`` git would see, relative to it, sorted."""
    return scan(root).files
