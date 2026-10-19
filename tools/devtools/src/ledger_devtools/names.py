"""Third-party distributions and the modules they install: what deptry and import-linter are told.

What a distribution installs is read from the environment's metadata (``packages_distributions``), never from
a list kept by hand: each importable top-level name, private ones (``_pytest``) and names no import can spell
(``lxml-stubs``) left out. A distribution that is not installed is assumed to install its own name.
"""

import functools
import re
from collections import defaultdict
from importlib.metadata import packages_distributions
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

_DISTRIBUTION: Final = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.ASCII)
_SEPARATORS: Final = re.compile(r"[-_.]+")
LOADED_BY_OTHERS: Final = frozenset({"grpcio-tools", "mypy-protobuf", "python-multipart", "tzdata"})
"""Distributions no module imports by name, installed for another to load.

``zoneinfo`` reads ``tzdata`` where the system has no time zone database (Windows); Starlette's form parser
imports ``multipart``; devtools loads protoc from ``grpcio-tools`` by its entry point (it ships no types), and
protoc runs ``mypy-protobuf``'s plugins as programs. deptry would call them unused.
"""
TEST_TOOLING: Final = ("pytest", "hypothesis", "testcontainers", "xdist", "ledger_testing")
"""What production code never imports (contract 13)."""


def distribution_of(requirement: str) -> str:
    """``psycopg[binary]>=3.3.6`` → ``psycopg``."""
    found = _DISTRIBUTION.match(requirement)
    return "" if found is None else found[0]


def _normalized(distribution: str) -> str:
    """The distribution's name as PEP 503 compares names: lower case, one ``-`` for each run of ``-_.``."""
    return _SEPARATORS.sub("-", distribution).lower()


@functools.cache
def _installed() -> Mapping[str, tuple[str, ...]]:
    """Each installed distribution (normalized) and the top-level modules it installs, sorted."""
    modules: defaultdict[str, set[str]] = defaultdict(set)
    for module, distributions in packages_distributions().items():
        if module.isidentifier() and not module.startswith("_"):
            for distribution in distributions:
                modules[_normalized(distribution)].add(module)
    return MappingProxyType({name: tuple(sorted(found)) for name, found in modules.items()})


def _own_name(distribution: str) -> str:
    return _normalized(distribution).replace("-", "_")


def import_names(distribution: str) -> tuple[str, ...]:
    """The top-level modules ``distribution`` installs."""
    return _installed().get(_normalized(distribution), (_own_name(distribution),))


def renamed(distribution: str) -> bool:
    """Whether ``distribution`` installs anything but its own name: deptry's module map lists those."""
    return import_names(distribution) != (_own_name(distribution),)
