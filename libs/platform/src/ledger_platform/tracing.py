"""Distributed tracing: one trace from a client's request through events and workers (W3C traceparent)."""

from contextlib import contextmanager
from typing import Final, final
lazy from collections.abc import Generator

import structlog
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
lazy from opentelemetry.sdk.trace.export import SpanExporter
lazy from opentelemetry.trace import Span, Tracer

_PROPAGATOR: Final = TraceContextTextMapPropagator()
_TRACEPARENT: Final = "traceparent"
_logger = structlog.stdlib.get_logger(__name__)


@final
class Tracing:
    """A tracer provider of the service's own, and the propagation of traces through events."""

    __slots__ = ("_provider", "_stopped", "_tracer")

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer("ledger_platform")
        self._stopped = False

    @classmethod
    def create(cls, *, service_name: str, exporter: SpanExporter | None) -> Tracing:
        """Spans named for ``service_name``, exported in batches when an exporter is given."""
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        return cls(provider)

    @property
    def provider(self) -> TracerProvider:
        """For setting the process-wide provider once, at service start."""
        return self._provider

    @property
    def tracer(self) -> Tracer:
        """The platform's tracer."""
        return self._tracer

    def current_traceparent(self) -> str | None:
        """The W3C ``traceparent`` of the active span, to carry in an event; ``None`` outside any span."""
        carrier: dict[str, str] = {}
        _PROPAGATOR.inject(carrier)
        return carrier.get(_TRACEPARENT)

    @contextmanager
    def consuming(self, name: str, traceparent: str | None) -> Generator[Span]:
        """A consumer span continuing ``traceparent``'s trace, or a new trace without a usable one.

        The parent comes from the event alone: whatever span happens to be active is never adopted. An
        exception raised inside is recorded on the span (status ``ERROR``, an ``exception`` event) and
        propagates unchanged, so a failed handling shows in the trace.
        """
        carrier: dict[str, str] = {_TRACEPARENT: traceparent} if traceparent else {}
        with self._tracer.start_as_current_span(
            name, context=_PROPAGATOR.extract(carrier), kind=SpanKind.CONSUMER
        ) as span:
            yield span

    def shutdown(self) -> None:
        """Export what is pending and stop; blocks while the exporter flushes.

        Only the first call does anything. An exporter that fails to flush or to stop (a collector that is
        gone) is logged and does not fail the shutdown: telemetry must not keep a service from stopping.
        """
        if self._stopped:
            return
        self._stopped = True
        try:
            self._provider.shutdown()
        except Exception:
            _logger.exception("tracing.shutdown_failed")
