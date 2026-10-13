"""The build context: what ``.dockerignore`` must keep out of every image built from the repository's root.

``docker build`` sends the whole context to the builder and ``COPY . .`` copies whatever reached it, so
a local secret left in the working tree (an ``.env`` file, the DGII certificate and its password under
``.dgii/``, a PKCS #12 key store) would land in a layer. ``check`` asks ``.dockerignore`` to name each
pattern, and refuses an exception (``!...``) that would bring one back.
"""

from fnmatch import fnmatch
from typing import Final
lazy from pathlib import Path

from ledger_devtools.checks import Problem
from ledger_devtools.images import dockerfiles

DOCKERIGNORE: Final = ".dockerignore"
SECRET_EXCLUSIONS: Final = (
    ".env",
    "**/.env",
    "**/.env.*",
    ".dgii",
    "**/.dgii",
    "*.p12",
    "**/*.p12",
    "*.pfx",
    "**/*.pfx",
)
"""Patterns ``.dockerignore`` must hold, each as written (Docker matches from the context's root)."""
_SECRET_NAMES: Final = frozenset(pattern.rsplit("/", 1)[-1] for pattern in SECRET_EXCLUSIONS)


def _secret(path: str) -> bool:
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return any(fnmatch(name, pattern) for pattern in _SECRET_NAMES)


def context_problems(root: Path) -> tuple[Problem, ...]:
    """Each secret pattern ``.dockerignore`` lacks, then each exception that re-includes a secret's name.

    A tree without a Dockerfile builds no image and needs no ``.dockerignore``.
    """
    if not dockerfiles(root):
        return ()
    path = root / DOCKERIGNORE
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
    return (
        *(
            Problem(
                DOCKERIGNORE, f"does not exclude '{pattern}': a secret could enter an image's build context"
            )
            for pattern in SECRET_EXCLUSIONS
            if pattern not in lines
        ),
        *(
            Problem(DOCKERIGNORE, f"re-includes '{line}', which may be a secret")
            for line in lines
            if line.startswith("!") and _secret(line[1:])
        ),
    )
