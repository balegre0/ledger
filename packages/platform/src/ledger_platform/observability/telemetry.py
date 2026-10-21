"""A package's telemetry: its logger, its tracer and the registry its metrics are exported with (§11)."""

from dataclasses import dataclass
from typing import final

import structlog
from prometheus_client import CollectorRegistry
lazy from opentelemetry.trace import Tracer

from ledger_platform.observability.tracing import Tracing


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Telemetry:
    """A package's logger, tracer and metrics registry."""

    logger: structlog.stdlib.BoundLogger
    """Bound to ``package=<name>``."""
    tracer: Tracer
    """Named after the package."""
    registry: CollectorRegistry
    """The app's registry: a package's metrics are exported with the app's."""

    @classmethod
    def of(cls, name: str, tracing: Tracing, registry: CollectorRegistry) -> Telemetry:
        """Telemetry scoped to the package ``name``."""
        return cls(
            logger=structlog.stdlib.get_logger(name).bind(package=name),
            tracer=tracing.provider.get_tracer(name),
            registry=registry,
        )

    @classmethod
    def unexported(cls, name: str) -> Telemetry:
        """Telemetry for ``name`` outside an app (a command-line tool, a test).

        Logs go where logging is configured; spans and metrics are made and stay in the process: no
        exporter, and a registry of its own.
        """
        return cls.of(name, Tracing.create(service_name=name, exporter=None), CollectorRegistry())
