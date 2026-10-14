"""What ``check`` asks of the repository around the manifests: the root's block, members, pins, the context.

The root ``pyproject.toml`` is written by hand but for its generated block (``ledger_devtools.root``); every
directory its member globs match must hold a manifest. ``sync`` fixes none of these, so a problem here never
stops it.
"""

import tomllib
from typing import ClassVar
lazy from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from ledger_devtools.checks import Problem
from ledger_devtools.context import context_problems
from ledger_devtools.images import image_problems, pin_problems, pinned_images
from ledger_devtools.root import ROOT_PYPROJECT, root_problems
lazy from ledger_devtools.workspace import Workspace


class _Lenient(BaseModel):
    """A table of the root file: only the keys read here; the rest is the tools' business."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class _UvWorkspace(_Lenient):
    members: tuple[str, ...] = ()


class _Uv(_Lenient):
    workspace: _UvWorkspace = _UvWorkspace()


class _Tool(_Lenient):
    uv: _Uv = _Uv()


class _Root(_Lenient):
    tool: _Tool = _Tool()


def _members_without_manifests(workspace: Workspace) -> Iterator[Problem]:
    """Every directory the root's member globs match without a manifest: no loop of the gate would see it."""
    path = workspace.root / ROOT_PYPROJECT
    if not path.is_file():
        return
    root = _Root.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    members = sorted(
        {
            found
            for pattern in root.tool.uv.workspace.members
            for found in workspace.root.glob(pattern)
            if found.is_dir() and not found.name.startswith((".", "__"))
        }
    )
    for member in members:
        if not (member / "manifest.toml").is_file():
            yield Problem(
                member.relative_to(workspace.root).as_posix(),
                "a workspace member without manifest.toml: the gate would leave it out",
            )


def repository_problems(workspace: Workspace) -> tuple[Problem, ...]:
    """The root's block, members without manifests, unpinned images, an unsafe context."""
    return (
        *root_problems(workspace),
        *_members_without_manifests(workspace),
        *pin_problems(workspace.root),
        *image_problems(workspace.root, pinned_images(workspace.root)),
        *context_problems(workspace.root),
    )
