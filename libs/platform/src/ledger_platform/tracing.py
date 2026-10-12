"""Distributed tracing: one trace from a client's request through events and workers (W3C traceparent)."""

from contextlib import contextmanager
from typing import Final, final
lazy from collections.abc import Generator

import structlog
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
lazy from opentelemetry.sdk.trace.export import SpanExporter
lazy from opentelemetry.trace import Span, Tracer

_PROPAGATOR: Final = TraceContextTextMapPropagator()
_TRACEPARENT: Final = "traceparent"
_MAX_TRACEPARENT_LENGTH: Final = 55
"""A version 00 ``traceparent``: 2 + 1 + 32 + 1 + 16 + 1 + 2 characters, all that ``Envelope`` accepts.

The SDK matches whatever it is given with a pattern that backtracks quadratically (a valid start followed by
tens of thousands of spaces takes seconds, on the event loop), so a longer value is never handed to it.
"""
_EXCEPTION: Final = "exception"
_EXCEPTION_TYPE: Final = "exception.type"
_logger = structlog.stdlib.get_logger(__name__)


def _type_name(error: Exception) -> str:
    """The qualified class name of ``error``, spelled as OpenTelemetry spells it (no ``builtins.``)."""
    kind = type(error)
    return kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"


def _carrier(traceparent: str | None) -> dict[str, str]:
    """``traceparent`` to extract from, or nothing to extract when it is absent or too long to be one."""
    if traceparent and len(traceparent) <= _MAX_TRACEPARENT_LENGTH:
        return {_TRACEPARENT: traceparent}
    return {}


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

        The parent comes from the event alone: whatever span happens to be active is never adopted, and a
        value longer than any ``traceparent`` is not even read.

        An ``Exception`` raised inside marks the span failed and propagates unchanged, so a failed handling
        shows in the trace. The span gets the error's type and nothing else (no message, no traceback): spans
        leave the process and cannot be recalled, and the message of a database or validation error can hold
        personal data. The detail belongs to the caller's log. A cancellation is not a failure: the status
        stays unset.
        """
        with self._tracer.start_as_current_span(
            name,
            context=_PROPAGATOR.extract(_carrier(traceparent)),
            kind=SpanKind.CONSUMER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event(_EXCEPTION, {_EXCEPTION_TYPE: _type_name(error)})
                raise

    def shutdown(self) -> None:
        """Export what is pending and stop.

        Blocking: the batch worker flushes and the exporter stops before this returns, so asynchronous code
        runs it with ``asyncio.to_thread``, never directly on the event loop.

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
