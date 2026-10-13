"""What ``check`` asks of the repository around the manifests: the root's lists and the image pins.

The root ``pyproject.toml`` is written by hand (the generator cannot own the file that configures it), so the
lists in it that name packages are compared with the workspace. ``sync`` writes neither, so a problem here
never stops it.
"""

import tomllib
from typing import ClassVar, Final
lazy from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field

from ledger_devtools.checks import Problem
from ledger_devtools.images import image_problems, pinned_images
from ledger_devtools.names import distribution_of
lazy from ledger_devtools.workspace import Workspace

ROOT_PYPROJECT: Final = "pyproject.toml"


class _Lenient(BaseModel):
    """A table of the root file: only the keys read here; the rest is the tools' business."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class _Isort(_Lenient):
    known_first_party: tuple[str, ...] = Field(default=(), alias="known-first-party")


class _Lint(_Lenient):
    isort: _Isort = _Isort()


class _Ruff(_Lenient):
    lint: _Lint = _Lint()


class _UvWorkspace(_Lenient):
    members: tuple[str, ...] = ()


class _Uv(_Lenient):
    sources: dict[str, object] = Field(default_factory=dict)
    workspace: _UvWorkspace = _UvWorkspace()


class _Tool(_Lenient):
    ruff: _Ruff = _Ruff()
    uv: _Uv = _Uv()


class _Groups(_Lenient):
    dev: tuple[str, ...] = ()


class _Root(_Lenient):
    tool: _Tool = _Tool()
    dependency_groups: _Groups = Field(default=_Groups(), alias="dependency-groups")


def _root_lists(workspace: Workspace) -> Iterator[Problem]:
    path = workspace.root / ROOT_PYPROJECT
    if not path.is_file():
        return
    root = _Root.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    packages = workspace.packages
    if set(root.tool.ruff.lint.isort.known_first_party) != {package.import_name for package in packages}:
        yield Problem(ROOT_PYPROJECT, "known-first-party is not the workspace's import packages")
    if set(root.tool.uv.sources) != {package.distribution for package in packages}:
        yield Problem(ROOT_PYPROJECT, "[tool.uv.sources] is not the workspace's distributions")
    distributions = {package.distribution for package in packages}
    dev = {
        name
        for requirement in root.dependency_groups.dev
        if (name := distribution_of(requirement)) in distributions
    }
    if dev != {package.distribution for package in packages if not package.is_production}:
        yield Problem(
            ROOT_PYPROJECT,
            "the dev dependency group does not name exactly the workspace's dev-only distributions",
        )


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
    """The root lists that differ from the workspace, members without a manifest, images not pinned."""
    return (
        *_root_lists(workspace),
        *_members_without_manifests(workspace),
        *image_problems(workspace.root, pinned_images(workspace.root)),
    )
