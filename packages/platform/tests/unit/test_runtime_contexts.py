from types import MappingProxyType

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs

from ledger_platform.observability.tracing import Tracing
from ledger_platform.runtime.contexts import (
    AssembledChannels,
    ChannelParts,
    ChannelsNotAssembledError,
    ChannelsSealedError,
    ChannelUnknownError,
    DuplicateChannelError,
    ModuleParts,
    Telemetry,
)


def test_telemetry_is_scoped_to_its_package() -> None:
    registry = CollectorRegistry()
    tracing = Tracing.create(service_name="probe", exporter=None)
    telemetry = Telemetry.of("invoicing", tracing, registry)
    assert telemetry.registry is registry
    with capture_logs() as logs:
        telemetry.logger.info("probe.said")
    assert logs == [{"event": "probe.said", "package": "invoicing", "log_level": "info"}]
    with telemetry.tracer.start_as_current_span("probe") as span:
        assert isinstance(span, ReadableSpan)
        assert span.instrumentation_scope is not None
        assert span.instrumentation_scope.name == "invoicing"


def test_parts_contribute_nothing_by_default() -> None:
    parts = ModuleParts()
    assert (parts.api, parts.implements, parts.provides, parts.routers) == (None, (), (), ())
    assert (parts.consumers, parts.jobs, parts.resources, parts.migrations) == ((), (), (), None)
    assert isinstance(parts.checks, MappingProxyType)
    assert not parts.checks
    channel = ChannelParts(channel="probe")
    assert (channel.routers, channel.receiver_routers, channel.consumers, channel.jobs) == ((), (), (), ())
    assert (dict(channel.checks), channel.resources, channel.migrations) == ({}, (), None)


def test_assembled_channels_are_read_only_once_sealed() -> None:
    channels = AssembledChannels()
    with pytest.raises(ChannelsNotAssembledError):
        channels.keys()
    channels.add("us-probe-v1", "probe")
    with pytest.raises(ChannelsNotAssembledError):
        channels.get("us-probe-v1")
    channels.seal()
    assert channels.keys() == ("us-probe-v1",)
    assert channels.get("us-probe-v1") == "probe"
    with pytest.raises(ChannelUnknownError, match="'us-other-v1'"):
        channels.get("us-other-v1")


def test_a_channel_added_twice_or_after_the_seal_is_a_defect() -> None:
    channels = AssembledChannels()
    channels.add("us-probe-v1", "probe")
    with pytest.raises(DuplicateChannelError, match="'us-probe-v1'"):
        channels.add("us-probe-v1", "again")
    channels.seal()
    with pytest.raises(ChannelsSealedError):
        channels.add("us-other-v1", "late")
    with pytest.raises(ChannelsSealedError):
        channels.seal()
