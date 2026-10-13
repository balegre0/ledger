"""Distributed tracing: one trace from a client's request through events and workers (W3C traceparent).

Spans leave the process and cannot be recalled, so what a span may carry is decided here, once: the type of an
exception, never its message or its traceback (they can quote personal data), and never the path or the query
string a client sent (a path carries the values of its parameters). The platform's own spans record no more
than that; every exporter is given the spans of others (a service's code, FastAPI's instrumentation) through
``RedactingSpanExporter``.
"""

import threading
from contextlib import contextmanager
from typing import Final, final, override
lazy from collections.abc import Generator, Sequence

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import ProxyTracerProvider, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
lazy from opentelemetry.sdk.trace.export import SpanExportResult
lazy from opentelemetry.trace import Span, Tracer
lazy from opentelemetry.util.types import Attributes

from ledger_kernel.errors import error_type

_PROPAGATOR: Final = TraceContextTextMapPropagator()
_TRACEPARENT: Final = "traceparent"
_MAX_TRACEPARENT_LENGTH: Final = 55
"""A version 00 ``traceparent``: 2 + 1 + 32 + 1 + 16 + 1 + 2 characters, all that ``Envelope`` accepts.

The SDK matches whatever it is given with a pattern that backtracks quadratically (a valid start followed by
tens of thousands of spaces takes seconds, on the event loop), so a longer value is never handed to it.
"""
_EXCEPTION: Final = "exception"
_EXCEPTION_TYPE: Final = "exception.type"
_REDACTED_EVENT_ATTRIBUTES: Final = frozenset({"exception.message", "exception.stacktrace"})
"""What OpenTelemetry records of an exception besides its type: its text and its traceback."""
_REDACTED_SPAN_ATTRIBUTES: Final = frozenset({"url.path", "url.query", "url.full", "http.target", "http.url"})
"""What an instrumentation copies from a request as it came: the path, with the values of its parameters
(``/taxpayers/00112345678``), and the query string, whatever a client chose to put in it, in the names of the
current semantic conventions and of the older ones. The span's name and ``http.route`` keep the template."""
DEFAULT_SHUTDOWN_TIMEOUT: Final = 4.0
"""Seconds a shutdown waits for the pending spans to be exported before it leaves them behind."""
_logger = structlog.stdlib.get_logger(__name__)


def _without(attributes: Attributes, names: frozenset[str]) -> Attributes:
    """``attributes`` less ``names``; the very same object when it has none of them."""
    if attributes is None or names.isdisjoint(attributes):
        return attributes
    return {name: value for name, value in attributes.items() if name not in names}


def _redacted_event(event: Event) -> Event:
    attributes = _without(event.attributes, _REDACTED_EVENT_ATTRIBUTES)
    return event if attributes is event.attributes else Event(event.name, attributes, event.timestamp)


def _redacted(span: ReadableSpan) -> ReadableSpan:
    """``span`` without what ``RedactingSpanExporter`` removes, or ``span`` itself when there is nothing to.

    ``span.attributes`` is a new view at each read: read once, an unchanged one is the very same object.
    """
    attributes = span.attributes
    kept = _without(attributes, _REDACTED_SPAN_ATTRIBUTES)
    events = tuple(_redacted_event(event) for event in span.events)
    unchanged = all(new is old for new, old in zip(events, span.events, strict=True))
    if kept is attributes and unchanged and span.status.description is None:
        return span
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=kept,
        events=events,
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


@final
class RedactingSpanExporter(SpanExporter):
    """Hands spans on to another exporter without what can carry personal data.

    Code that opens spans of its own, and the instrumentation of frameworks, record an exception the
    OpenTelemetry way: its message and its traceback as attributes of an ``exception`` event, and ``Type:
    message`` as the description of the status. The message of a database error can quote a row (``Key
    (tax_id)=(...)``), that of a validation error what a client sent. Whoever recorded them, the spans leave
    without ``exception.message`` and ``exception.stacktrace``, without the description of their status, and
    without the path and the query string an instrumentation copies from a request (``url.path``,
    ``url.query``; ``url.full``, ``http.target`` and ``http.url`` in other conventions): a path carries the
    values of its parameters. The type of the exception stays, and so does the rest, the span's name and its
    ``http.route`` included: they are the route's template.

    A span with nothing to remove is handed on as it is. One that is rebuilt no longer tells how many
    attributes, events and links the SDK dropped from it for exceeding its limits.
    """

    __slots__ = ("_exporter",)

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export([_redacted(span) for span in spans])

    @override
    def shutdown(self) -> None:
        self._exporter.shutdown()

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)


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
        """Spans named for ``service_name``, redacted and exported in batches when an exporter is given."""
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(RedactingSpanExporter(exporter)))
        return cls(provider)

    @property
    def provider(self) -> TracerProvider:
        """For the instrumentation that is given a provider, such as FastAPI's."""
        return self._provider

    def register_globally(self) -> None:
        """Make this provider the process-wide one, unless one already is.

        Instrumentation that is given no provider uses the process-wide one, which OpenTelemetry takes once,
        warning about every later attempt. A process runs one service, so the first is the only one; a second
        service of the same process (a test) keeps its provider to itself, without the warning.
        """
        if isinstance(trace.get_tracer_provider(), ProxyTracerProvider):
            trace.set_tracer_provider(self._provider)

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
                span.add_event(_EXCEPTION, {_EXCEPTION_TYPE: error_type(error)})
                raise

    def shutdown(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT) -> None:
        """Export what is pending and stop, giving up after ``timeout`` seconds.

        Blocking: it waits for the batch worker to flush and the exporter to stop, so asynchronous code runs
        it with ``asyncio.to_thread``, never directly on the event loop. The work runs on a daemon thread of
        its own: one still running after ``timeout`` (a collector that does not answer) is logged as
        ``tracing.shutdown_timed_out`` and left behind, and never holds up the process's exit.

        Only the first call does anything. An exporter that fails to flush or to stop (a collector that is
        gone) is logged and does not fail the shutdown: telemetry must not keep a service from stopping.
        """
        if self._stopped:
            return
        self._stopped = True
        stopping = threading.Thread(target=self._stop_provider, name="tracing-shutdown", daemon=True)
        stopping.start()
        stopping.join(timeout)
        if stopping.is_alive():
            _logger.warning("tracing.shutdown_timed_out", timeout=timeout)

    def _stop_provider(self) -> None:
        try:
            self._provider.shutdown()
        except Exception:
            _logger.exception("tracing.shutdown_failed")
