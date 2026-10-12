import asyncio
from time import perf_counter
from typing import Final, final, override
lazy from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, StatusCode, TraceFlags, use_span
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace import ReadableSpan
lazy from opentelemetry.sdk.trace.export import SpanExportResult

from ledger_contracts.envelope import Envelope
from ledger_kernel.clock import utc_now
from ledger_kernel.ids import new_id
from ledger_platform.tracing import Tracing

TRACEPARENT: Final = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
SENSITIVE: Final = "Key (tax_id)=(001-1234567-8) already exists"
"""What a database error says: personal data that must never leave the process in a span."""


@final
class PoisonError(Exception):
    """An error of the tests' own, to tell its qualified name from a built-in's."""


@final
class CountingExporter(InMemorySpanExporter):
    """Remembers how often it was shut down."""

    def __init__(self) -> None:
        super().__init__()
        self.shutdowns = 0

    @override
    def shutdown(self) -> None:
        self.shutdowns += 1
        super().shutdown()


@final
class RejectingExporter(SpanExporter):
    """The collector answers with errors: every export fails, and the attempts are counted."""

    def __init__(self) -> None:
        self.attempts = 0

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.attempts += 1
        raise ConnectionError


@final
class UnreachableExporter(SpanExporter):
    """The collector is gone: exporting fails, and so does shutting down."""

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError

    @override
    def shutdown(self) -> None:
        raise ConnectionError


def event_carrying(traceparent: str | None) -> Envelope:
    return Envelope(
        id=new_id(),
        type="ledger.events.invoicing.v1.DocumentIssued",
        source="invoicing",
        tenant=None,
        time=utc_now(),
        data=b"",
        traceparent=traceparent,
    )


@pytest.fixture(scope="module")
def unexported() -> Tracing:
    """Spans are made and dropped; for tests that check the trace context, not what was exported."""
    return Tracing(TracerProvider(shutdown_on_exit=False))


def test_a_consumer_continues_the_producer_s_trace(tracing: Tracing, exporter: InMemorySpanExporter) -> None:
    with tracing.tracer.start_as_current_span("produce"):
        traceparent = tracing.current_traceparent()
    assert traceparent is not None
    with tracing.consuming("consume invoicing", traceparent):
        pass
    consume, produce = sorted(exporter.get_finished_spans(), key=lambda span: span.name)
    assert consume.context is not None
    assert produce.context is not None
    assert consume.context.trace_id == produce.context.trace_id
    assert consume.parent is not None
    assert consume.parent.span_id == produce.context.span_id
    assert consume.kind is SpanKind.CONSUMER


def test_without_an_active_span_there_is_no_traceparent(tracing: Tracing) -> None:
    assert tracing.current_traceparent() is None


@pytest.mark.parametrize(
    "traceparent",
    [
        None,
        "",
        "garbage",
        "00-" + "0" * 32 + "-b7ad6b7169203331-01",
        "00-0af7651916cd43dd8448eb211c80319c-" + "0" * 16 + "-01",
        TRACEPARENT.upper(),
        TRACEPARENT.removesuffix("-01"),
        TRACEPARENT + "-extra",
        " " + TRACEPARENT,
    ],
    ids=[
        "none",
        "empty",
        "garbage",
        "zero-trace-id",
        "zero-parent-id",
        "uppercase",
        "no-flags",
        "suffix",
        "padded",
    ],
)
def test_without_a_usable_parent_a_new_trace_starts(
    tracing: Tracing, exporter: InMemorySpanExporter, traceparent: str | None
) -> None:
    with tracing.consuming("consume", traceparent):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.parent is None


def test_a_hostile_traceparent_costs_no_time_and_starts_a_new_trace(
    tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    """A valid start and a long padding: the SDK's own pattern backtracks quadratically on it (seconds)."""
    hostile = TRACEPARENT + "-" + " " * 50_000 + "\nx"
    started = perf_counter()
    with tracing.consuming("consume", hostile):
        pass
    assert perf_counter() - started < 0.1
    (span,) = exporter.get_finished_spans()
    assert span.parent is None


def test_a_consumer_without_a_parent_does_not_adopt_the_span_that_happens_to_be_active(
    tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    with tracing.tracer.start_as_current_span("ambient"), tracing.consuming("consume", None):
        pass
    consume, ambient = sorted(exporter.get_finished_spans(), key=lambda span: span.name)
    assert consume.parent is None
    assert consume.context is not None
    assert ambient.context is not None
    assert consume.context.trace_id != ambient.context.trace_id


@pytest.mark.parametrize(
    ("kind", "named"),
    [(PoisonError, f"{PoisonError.__module__}.PoisonError"), (RuntimeError, "RuntimeError")],
    ids=["own", "built-in"],
)
def test_a_failing_consumer_is_recorded_by_error_type_alone_and_the_error_is_raised_unchanged(
    tracing: Tracing, exporter: InMemorySpanExporter, kind: type[Exception], named: str
) -> None:
    """Spans leave the process and cannot be recalled: the message and the traceback stay in the logs."""
    failure = kind(SENSITIVE)
    with pytest.raises(kind) as raised, tracing.consuming("consume invoicing", None):
        raise failure
    assert raised.value is failure
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    (event,) = span.events
    assert event.name == "exception"
    assert event.attributes == {"exception.type": named}
    assert "tax_id" not in span.to_json()
    assert tracing.current_traceparent() is None


def test_a_cancelled_consumer_is_not_reported_as_a_failure(
    tracing: Tracing, exporter: InMemorySpanExporter
) -> None:
    with pytest.raises(asyncio.CancelledError), tracing.consuming("consume invoicing", None):
        raise asyncio.CancelledError
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.UNSET
    assert span.events == ()


@given(
    trace_id=st.integers(min_value=1, max_value=2**128 - 1),
    span_id=st.integers(min_value=1, max_value=2**64 - 1),
    sampled=st.booleans(),
)
def test_whatever_trace_is_active_travels_through_an_event_and_is_continued(
    unexported: Tracing, trace_id: int, span_id: int, *, sampled: bool
) -> None:
    flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
    active = NonRecordingSpan(SpanContext(trace_id, span_id, is_remote=False, trace_flags=flags))
    with use_span(active):
        traceparent = unexported.current_traceparent()
    assert traceparent is not None
    event = event_carrying(traceparent)
    received = Envelope.from_headers(event.headers(), event.data)
    with unexported.consuming("consume", received.traceparent) as consumer:
        continued = consumer.get_span_context()
    assert continued.trace_id == trace_id
    assert continued.trace_flags.sampled is sampled


def test_the_provider_is_the_one_given_for_the_service_to_register_process_wide() -> None:
    provider = TracerProvider(shutdown_on_exit=False)
    assert Tracing(provider).provider is provider


def test_create_names_the_service_and_exports_only_with_an_exporter(exporter: InMemorySpanExporter) -> None:
    exporting = Tracing.create(service_name="invoicing", exporter=exporter)
    with exporting.tracer.start_as_current_span("work"):
        pass
    exporting.shutdown()
    (span,) = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == "invoicing"

    silent = Tracing.create(service_name="invoicing", exporter=None)
    with silent.tracer.start_as_current_span("work"):
        pass
    silent.shutdown()


def test_shutdown_stops_the_exporter_once_however_often_it_is_asked() -> None:
    exporter = CountingExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = Tracing(provider)
    tracing.shutdown()
    tracing.shutdown()
    assert exporter.shutdowns == 1


def test_spans_the_exporter_rejects_at_the_flush_do_not_fail_the_shutdown() -> None:
    exporter = RejectingExporter()
    tracing = Tracing.create(service_name="invoicing", exporter=exporter)
    with tracing.tracer.start_as_current_span("pending"):
        pass
    tracing.shutdown()
    assert exporter.attempts == 1


def test_an_exporter_that_fails_to_stop_is_logged_and_does_not_fail_the_shutdown() -> None:
    tracing = Tracing.create(service_name="invoicing", exporter=UnreachableExporter())
    with tracing.tracer.start_as_current_span("pending"):
        pass
    with capture_logs() as logs:
        tracing.shutdown()
    assert logs == [{"event": "tracing.shutdown_failed", "log_level": "error", "exc_info": True}]
    tracing.shutdown()
