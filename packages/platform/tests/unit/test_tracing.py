import asyncio
import threading
from time import monotonic, perf_counter
from typing import Final, final, override
lazy from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, StatusCode, TraceFlags, use_span
from structlog.testing import capture_logs
lazy from opentelemetry.sdk.trace import ReadableSpan

from ledger_platform.observability.tracing import RedactingSpanExporter, Tracing

TRACEPARENT: Final = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
SENSITIVE: Final = "Key (tax_id)=(001-1234567-8) already exists"
"""What a database error says: personal data that must never leave the process in a span."""
RNC: Final = "00112345678"
"""A national id, as a client sends it in a path or a query string."""
QUERY: Final = f"rnc={RNC}&page=2"


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


@final
class DelegationProbe(SpanExporter):
    """Records what it is asked and answers as told: a wrapper is seen to pass questions and answers on."""

    def __init__(self, *, result: SpanExportResult, flushed: bool) -> None:
        self.exported: list[Sequence[ReadableSpan]] = []
        self.flushes: list[int] = []
        self.shutdowns = 0
        self._result = result
        self._flushed = flushed

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.exported.append(spans)
        return self._result

    @override
    def shutdown(self) -> None:
        self.shutdowns += 1

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flushes.append(timeout_millis)
        return self._flushed


def redacting(exporter: SpanExporter) -> Tracing:
    """A tracing whose finished spans go through ``RedactingSpanExporter`` to ``exporter`` at once."""
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(exporter)))
    return Tracing(provider)


def work(tracing: Tracing, *, record_exception: bool, set_status_on_exception: bool) -> None:
    """Fails inside a span opened the OpenTelemetry way, as a service's code or an instrumentation does: by
    default the exception is recorded with its message and traceback, and the status describes it."""
    with tracing.tracer.start_as_current_span(
        "work", record_exception=record_exception, set_status_on_exception=set_status_on_exception
    ):
        raise PoisonError(SENSITIVE)


def fail_in_a_span(
    tracing: Tracing, *, record_exception: bool = True, set_status_on_exception: bool = True
) -> None:
    with pytest.raises(PoisonError):
        work(tracing, record_exception=record_exception, set_status_on_exception=set_status_on_exception)


def answer_a_query(tracing: Tracing) -> None:
    """A request span as FastAPI records it: the route, and the query string as the client sent it."""
    with tracing.tracer.start_as_current_span("GET /customers") as span:
        span.set_attributes({"http.route": "/customers", "url.query": QUERY})


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
def test_whatever_trace_is_active_is_continued_from_its_traceparent(
    unexported: Tracing, trace_id: int, span_id: int, *, sampled: bool
) -> None:
    flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
    active = NonRecordingSpan(SpanContext(trace_id, span_id, is_remote=False, trace_flags=flags))
    with use_span(active):
        traceparent = unexported.current_traceparent()
    assert traceparent is not None
    with unexported.consuming("consume", traceparent) as consumer:
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


def test_an_exported_exception_keeps_its_type_and_loses_its_message_and_traceback(
    exporter: InMemorySpanExporter,
) -> None:
    fail_in_a_span(redacting(exporter), set_status_on_exception=False)
    (span,) = exporter.get_finished_spans()
    (event,) = span.events
    assert event.name == "exception"
    assert event.attributes is not None
    assert event.attributes["exception.type"] == f"{PoisonError.__module__}.PoisonError"
    assert "exception.message" not in event.attributes
    assert "exception.stacktrace" not in event.attributes
    assert "tax_id" not in span.to_json()


def test_a_failed_span_leaves_with_its_status_and_without_its_description(
    exporter: InMemorySpanExporter,
) -> None:
    fail_in_a_span(redacting(exporter), record_exception=False)
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.events == ()


def test_the_query_string_a_client_sent_never_leaves_in_a_span(exporter: InMemorySpanExporter) -> None:
    answer_a_query(redacting(exporter))
    (span,) = exporter.get_finished_spans()
    assert span.attributes == {"http.route": "/customers"}


@pytest.mark.parametrize("attribute", ["url.path", "url.full", "http.target", "http.url"])
def test_the_path_a_client_sent_never_leaves_in_a_span_and_the_route_template_does(
    exporter: InMemorySpanExporter, attribute: str
) -> None:
    """A path carries the values of its parameters (``/taxpayers/{rnc}``); its template carries none."""
    with redacting(exporter).tracer.start_as_current_span("GET /taxpayers/{rnc}") as span:
        span.set_attributes({"http.route": "/taxpayers/{rnc}", attribute: f"/taxpayers/{RNC}"})
    (exported,) = exporter.get_finished_spans()
    assert exported.name == "GET /taxpayers/{rnc}"
    assert exported.attributes == {"http.route": "/taxpayers/{rnc}"}


def test_a_redacted_span_is_otherwise_the_span_that_was_recorded(exporter: InMemorySpanExporter) -> None:
    recorded = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(recorded))
    provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(exporter)))
    tracing = Tracing(provider)
    with tracing.tracer.start_as_current_span("parent"):
        fail_in_a_span(tracing)
    original = next(span for span in recorded.get_finished_spans() if span.name == "work")
    redacted = next(span for span in exporter.get_finished_spans() if span.name == "work")
    assert redacted is not original
    assert (redacted.context, redacted.parent, redacted.kind) == (
        original.context,
        original.parent,
        original.kind,
    )
    assert (redacted.start_time, redacted.end_time) == (original.start_time, original.end_time)
    assert redacted.resource is original.resource
    assert redacted.instrumentation_scope == original.instrumentation_scope
    assert redacted.links == original.links
    assert [(event.name, event.timestamp) for event in redacted.events] == [
        (event.name, event.timestamp) for event in original.events
    ]


def test_a_span_with_nothing_to_hide_is_handed_on_as_it_is(exporter: InMemorySpanExporter) -> None:
    recorded = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(recorded))
    provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(exporter)))
    with provider.get_tracer("test").start_as_current_span("work") as span:
        span.set_attribute("http.route", "/customers")
        span.add_event("retried", {"attempt": 2})
    assert exporter.get_finished_spans() == recorded.get_finished_spans()


@pytest.mark.parametrize("result", list(SpanExportResult))
def test_the_redacting_exporter_answers_what_the_one_it_wraps_answers(result: SpanExportResult) -> None:
    probe = DelegationProbe(result=result, flushed=False)
    assert RedactingSpanExporter(probe).export([]) is result
    assert probe.exported == [[]]


@pytest.mark.parametrize("flushed", [True, False])
def test_the_redacting_exporter_flushes_and_stops_the_one_it_wraps(*, flushed: bool) -> None:
    probe = DelegationProbe(result=SpanExportResult.SUCCESS, flushed=flushed)
    redacting_exporter = RedactingSpanExporter(probe)
    assert redacting_exporter.force_flush(1234) is flushed
    redacting_exporter.shutdown()
    assert (probe.flushes, probe.shutdowns) == ([1234], 1)


def test_every_exporter_a_tracing_is_created_with_gets_redacted_spans(exporter: InMemorySpanExporter) -> None:
    tracing = Tracing.create(service_name="invoicing", exporter=exporter)
    fail_in_a_span(tracing)
    answer_a_query(tracing)
    tracing.shutdown()
    exported = "".join(span.to_json() for span in exporter.get_finished_spans())
    assert [span.name for span in exporter.get_finished_spans()] == ["work", "GET /customers"]
    assert "tax_id" not in exported
    assert "00112345678" not in exported


def test_the_first_tracing_made_process_wide_stays_so_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OpenTelemetry takes one process-wide provider and warns about every later attempt to set another.

    Which provider holds the place depends on which test ran first. That it is an SDK provider, that it does
    not change, and that nobody is warned does not.
    """
    first = Tracing(TracerProvider(shutdown_on_exit=False))
    second = Tracing(TracerProvider(shutdown_on_exit=False))
    first.register_globally()
    registered = trace.get_tracer_provider()
    second.register_globally()
    assert isinstance(registered, TracerProvider)
    assert trace.get_tracer_provider() is registered
    assert [record for record in caplog.records if record.name.startswith("opentelemetry")] == []


@final
class HangingExporter(SpanExporter):
    """A collector that never answers: every export waits until the test lets it go."""

    def __init__(self) -> None:
        self.released = threading.Event()

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        del spans
        self.released.wait()
        return SpanExportResult.SUCCESS


def test_a_flush_that_hangs_is_left_behind_after_its_time_limit() -> None:
    """A collector that is gone must not keep the service from stopping: the stop goes on after the limit,
    and the export finishes (or not) on a thread that never holds up the process's exit."""
    exporter = HangingExporter()
    tracing = Tracing.create(service_name="invoicing", exporter=exporter)
    with tracing.tracer.start_as_current_span("pending"):
        pass
    started = monotonic()
    try:
        with capture_logs() as logs:
            tracing.shutdown(timeout=0.2)
        assert monotonic() - started < 2
        assert logs == [{"event": "tracing.shutdown_timed_out", "log_level": "warning", "timeout": 0.2}]
    finally:
        exporter.released.set()
