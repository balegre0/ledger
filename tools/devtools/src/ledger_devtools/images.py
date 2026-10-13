"""Image pins (Decision 20): ``deploy/local/images.toml`` is the only place an image version is spelled.

compose cannot read TOML, so ``compose.yaml`` and the Dockerfiles repeat the pins; ``check`` reports every
image they name that is not one of them. The image the repository builds (``ledger:<tag>``) is no pin.
"""

import re
import tomllib
from typing import Final
lazy from pathlib import Path

from pydantic import TypeAdapter

from ledger_devtools.checks import Problem

PINS: Final = "deploy/local/images.toml"
COMPOSE: Final = "deploy/local/compose.yaml"
DOCKERFILES: Final = "deploy/docker/*.Dockerfile"
_OWN: Final = re.compile(r"ledger:[A-Za-z0-9_.-]+", re.ASCII)
_COMPOSE_IMAGE: Final = re.compile(r"^\s*image:\s*[\"']?([^\"'\s#]+)", re.MULTILINE)
_FROM: Final = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.MULTILINE | re.IGNORECASE)
_IMAGES: Final = TypeAdapter(dict[str, dict[str, str]])


def pinned_images(root: Path) -> frozenset[str]:
    """The images ``deploy/local/images.toml`` pins (none when there is no such file)."""
    path = root / PINS
    if not path.is_file():
        return frozenset()
    return frozenset(
        _IMAGES.validate_python(tomllib.loads(path.read_text(encoding="utf-8")))["images"].values()
    )


def _named(root: Path) -> tuple[tuple[str, str], ...]:
    """Each image a deploy file names, with the file (relative to ``root``), file by file in path order."""
    compose = root / COMPOSE
    named: list[tuple[str, str]] = []
    if compose.is_file():
        named += (
            (COMPOSE, found[1]) for found in _COMPOSE_IMAGE.finditer(compose.read_text(encoding="utf-8"))
        )
    for dockerfile in sorted(root.glob(DOCKERFILES)):
        stages: set[str] = set()
        where = dockerfile.relative_to(root).as_posix()
        for found in _FROM.finditer(dockerfile.read_text(encoding="utf-8")):
            image, stage = found[1], found[2]
            if image not in stages:
                named.append((where, image))
            if stage is not None:
                stages.add(stage)
    return tuple(sorted(named))


def image_problems(root: Path, pins: frozenset[str]) -> tuple[Problem, ...]:
    """Every image of ``compose.yaml`` and the Dockerfiles that is neither a pin nor the repository's own."""
    return tuple(
        Problem(where, f"image '{image}' is not pinned in {PINS}")
        for where, image in _named(root)
        if image not in pins and _OWN.fullmatch(image) is None
    )
