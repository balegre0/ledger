from datetime import timedelta
from typing import final, override
lazy from collections.abc import Callable

import pytest
from nats.js.api import RetentionPolicy, StorageType, StreamConfig, StreamInfo, StreamState
from nats.js.errors import APIError, NotFoundError
lazy from nats.js import JetStreamContext

from ledger_platform.streams import (
    DEAD_LETTERS,
    InvalidStreamSpecError,
    StreamApi,
    StreamRefusedError,
    StreamSpec,
    ensure_stream,
)

STREAM = StreamSpec(name="TEST", subjects=("test.>",))


async def configuration(jetstream: JetStreamContext, name: str) -> StreamConfig:
    return (await jetstream.stream_info(name)).config


async def created_by_someone_else(api: StreamApi, config: StreamConfig) -> None:
    """A stream with a configuration ``ensure_stream`` would not have given it."""
    await api.add_stream(config)


async def test_a_missing_stream_is_created_as_specified(jetstream: JetStreamContext) -> None:
    spec = StreamSpec(
        name="TEST",
        subjects=("test.>", "more.>"),
        duplicate_window=timedelta(minutes=5),
        max_age=timedelta(days=3),
    )
    await ensure_stream(jetstream, spec)
    config = await configuration(jetstream, "TEST")
    assert config.subjects == ["test.>", "more.>"]
    assert config.duplicate_window == timedelta(minutes=5).total_seconds()
    assert config.max_age == timedelta(days=3).total_seconds()
    assert config.storage == StorageType.FILE


async def test_the_defaults_keep_two_minutes_of_duplicates_for_a_week(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, STREAM)
    config = await configuration(jetstream, "TEST")
    assert config.duplicate_window == timedelta(minutes=2).total_seconds()
    assert config.max_age == timedelta(days=7).total_seconds()


async def test_an_existing_stream_gets_the_new_subjects_and_limits(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, STREAM)
    await ensure_stream(
        jetstream,
        StreamSpec(
            name="TEST",
            subjects=("test.>", "more.>"),
            duplicate_window=timedelta(minutes=1),
            max_age=timedelta(days=1),
        ),
    )
    config = await configuration(jetstream, "TEST")
    assert config.subjects == ["test.>", "more.>"]
    assert config.duplicate_window == timedelta(minutes=1).total_seconds()
    assert config.max_age == timedelta(days=1).total_seconds()


async def test_ensuring_a_stream_twice_changes_nothing(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, STREAM)
    before = await configuration(jetstream, "TEST")
    await ensure_stream(jetstream, STREAM)
    assert await configuration(jetstream, "TEST") == before


async def test_what_the_spec_does_not_own_is_left_as_it_is(jetstream: JetStreamContext) -> None:
    """Storage, retention and the rest are whoever created the stream's to choose: no restart moves data."""
    await created_by_someone_else(
        jetstream,
        StreamConfig(
            name="TEST",
            subjects=["test.>"],
            storage=StorageType.MEMORY,
            retention=RetentionPolicy.WORK_QUEUE,
            max_msgs=500,
        ),
    )
    await jetstream.publish("test.created", b"kept")

    await ensure_stream(jetstream, StreamSpec(name="TEST", subjects=("test.>", "more.>")))

    info = await jetstream.stream_info("TEST")
    assert info.config.subjects == ["test.>", "more.>"]
    assert info.config.storage == StorageType.MEMORY
    assert info.config.retention == RetentionPolicy.WORK_QUEUE
    assert info.config.max_msgs == 500
    assert info.state.messages == 1


async def test_the_dead_letter_stream_stores_the_advisories_of_given_up_events(
    jetstream: JetStreamContext,
) -> None:
    await ensure_stream(jetstream, DEAD_LETTERS)
    config = await configuration(jetstream, "DEAD_LETTERS")
    assert config.subjects == [
        "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.>",
        "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.>",
    ]
    assert config.max_age == timedelta(days=30).total_seconds()


async def test_a_stream_nats_refuses_to_update_is_named_and_keeps_its_configuration(
    jetstream: JetStreamContext,
) -> None:
    await ensure_stream(jetstream, StreamSpec(name="FIRST", subjects=("first.>",)))
    await ensure_stream(jetstream, StreamSpec(name="SECOND", subjects=("second.>",)))

    with pytest.raises(StreamRefusedError, match="'SECOND'") as refused:
        await ensure_stream(jetstream, StreamSpec(name="SECOND", subjects=("first.>",)))

    assert isinstance(refused.value.__cause__, APIError)
    assert refused.value.code == "stream_refused"
    assert (await configuration(jetstream, "SECOND")).subjects == ["second.>"]


async def test_a_stream_nats_refuses_to_create_is_named(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, StreamSpec(name="FIRST", subjects=("first.>",)))

    with pytest.raises(StreamRefusedError, match="'SECOND'") as refused:
        await ensure_stream(jetstream, StreamSpec(name="SECOND", subjects=("first.>",)))

    assert isinstance(refused.value.__cause__, APIError)


@pytest.mark.parametrize(
    ("field", "spec"),
    [
        ("subjects", lambda: StreamSpec(name="TEST", subjects=())),
        ("duplicate_window", lambda: StreamSpec(name="TEST", subjects=("a",), duplicate_window=timedelta(0))),
        (
            "duplicate_window",
            lambda: StreamSpec(name="TEST", subjects=("a",), duplicate_window=-timedelta(1)),
        ),
        ("max_age", lambda: StreamSpec(name="TEST", subjects=("a",), max_age=timedelta(0))),
        ("max_age", lambda: StreamSpec(name="TEST", subjects=("a",), max_age=-timedelta(1))),
        (
            "duplicate_window",
            lambda: StreamSpec(
                name="TEST", subjects=("a",), duplicate_window=timedelta(days=2), max_age=timedelta(days=1)
            ),
        ),
    ],
)
def test_a_spec_that_would_misconfigure_the_stream_is_refused(
    field: str, spec: Callable[[], StreamSpec]
) -> None:
    """Nothing means "everything" to NATS: no subjects store the stream's own name, zero age never expires,
    and a window longer than the age is a stream NATS refuses."""
    with pytest.raises(InvalidStreamSpecError, match=field):
        spec()


def test_a_window_as_long_as_the_age_is_allowed() -> None:
    spec = StreamSpec(
        name="TEST", subjects=("a",), duplicate_window=timedelta(hours=1), max_age=timedelta(hours=1)
    )
    assert spec.duplicate_window == spec.max_age


@final
class Unserializable(StreamApi):
    """JetStream when nats-py cannot even build the request (it raises ``ValueError`` before sending)."""

    def __init__(self, *, exists: bool) -> None:
        self._exists = exists

    @override
    async def stream_info(self, name: str) -> StreamInfo:
        if not self._exists:
            raise NotFoundError
        return StreamInfo(config=StreamConfig(name=name), state=StreamState(0, 0, 0, 0, 0))

    @override
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        raise ValueError

    @override
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        raise ValueError


@pytest.mark.parametrize(
    "api", [Unserializable(exists=True), Unserializable(exists=False)], ids=["on update", "on create"]
)
async def test_a_request_nats_py_cannot_build_is_a_refused_stream_too(api: StreamApi) -> None:
    with pytest.raises(StreamRefusedError, match="'TEST'") as refused:
        await ensure_stream(api, STREAM)
    assert isinstance(refused.value.__cause__, ValueError)
