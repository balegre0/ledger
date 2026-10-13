"""The container images the repository pins, read from ``deploy/local/images.toml`` (one place, §13)."""

import tomllib
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from pydantic import TypeAdapter

IMAGES_FILE: Final = Path("deploy/local/images.toml")
_FILE: Final = TypeAdapter(dict[str, dict[str, str]])


class ImagesFileNotFoundError(FileNotFoundError):
    """Tests run inside the repository: no ``deploy/local/images.toml`` above the current directory."""

    def __init__(self, start: Path) -> None:
        super().__init__(f"no {IMAGES_FILE.as_posix()} in {start} or any directory above it")


@cache
def images() -> Mapping[str, str]:
    """Every pinned image by name, from the nearest ``deploy/local/images.toml`` here or above."""
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / IMAGES_FILE
        if candidate.is_file():
            return MappingProxyType(
                _FILE.validate_python(tomllib.loads(candidate.read_text("utf-8")))["images"]
            )
    raise ImagesFileNotFoundError(here)


def image(name: str) -> str:
    """The pinned image ``name`` (``postgres``, ``zitadel``, ...)."""
    return images()[name]
