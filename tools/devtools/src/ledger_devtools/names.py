"""Third-party distributions and the modules they install: what deptry and import-linter are told."""

import re
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

_DISTRIBUTION: Final = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.ASCII)
_IMPORTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "grpcio": ("grpc",),
        "opentelemetry-api": ("opentelemetry",),
        "opentelemetry-sdk": ("opentelemetry",),
        "opentelemetry-exporter-otlp-proto-grpc": ("opentelemetry",),
        "prometheus-client": ("prometheus_client",),
        "protobuf": ("google",),
        "pydantic-settings": ("pydantic_settings",),
        "pytest-asyncio": ("pytest_asyncio",),
        "pytest-xdist": ("xdist",),
    }
)
LOADED_BY_OTHERS: Final = frozenset({"python-multipart", "tzdata"})
"""Distributions no module imports by name, installed for another to load.

``zoneinfo`` reads ``tzdata`` where the system has no time zone database (Windows); Starlette's form parser
imports ``multipart``. deptry would call them unused.
"""
TEST_TOOLING: Final = ("pytest", "hypothesis", "testcontainers", "xdist", "ledger_testing")
"""What production code never imports (contract 13)."""


def distribution_of(requirement: str) -> str:
    """``psycopg[binary]>=3.3.6`` → ``psycopg``."""
    found = _DISTRIBUTION.match(requirement)
    return "" if found is None else found[0]


def renamed(distribution: str) -> bool:
    """Whether ``distribution`` installs modules named otherwise (deptry's module map lists exactly those)."""
    return distribution in _IMPORTS


def import_names(distribution: str) -> tuple[str, ...]:
    """The top-level modules ``distribution`` installs."""
    return _IMPORTS.get(distribution, (distribution.replace("-", "_"),))
