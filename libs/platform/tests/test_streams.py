from dataclasses import replace
from datetime import timedelta
from typing import Final, final, override
lazy from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nats.js.api import RetentionPolicy, StorageType, StreamConfig, StreamInfo, StreamState
from nats.js.errors import APIError, BadRequestError, NotFoundError, ServerError, ServiceUnavailableError
lazy from nats.js import JetStreamContext

from ledger_kernel.errors import ErrorKind
from ledger_platform.streams import (
    DEAD_LETTERS,
    MAX_ATTEMPTS,
    InvalidStreamSpecError,
    StreamApi,
    StreamNotSettledError,
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


async def test_an_existing_stream_gains_the_new_subjects_and_longer_limits(
    jetstream: JetStreamContext,
) -> None:
    await ensure_stream(jetstream, STREAM)
    await ensure_stream(
        jetstream,
        StreamSpec(
            name="TEST",
            subjects=("test.>", "more.>"),
            duplicate_window=timedelta(minutes=5),
            max_age=timedelta(days=30),
        ),
    )
    config = await configuration(jetstream, "TEST")
    assert config.subjects == ["test.>", "more.>"]
    assert config.duplicate_window == timedelta(minutes=5).total_seconds()
    assert config.max_age == timedelta(days=30).total_seconds()


async def test_an_older_spec_never_shrinks_a_stream(jetstream: JetStreamContext) -> None:
    """An older replica restarting during a rollout applies its own spec after a newer one: the stream keeps
    every subject and the longer limits, so no event is dropped and none expires early."""
    newer = StreamSpec(
        name="TEST",
        subjects=("test.>", "more.>"),
        duplicate_window=timedelta(minutes=5),
        max_age=timedelta(days=30),
    )
    older = StreamSpec(name="TEST", subjects=("test.>", "old.>"), duplicate_window=timedelta(minutes=1))
    await ensure_stream(jetstream, newer)
    await jetstream.publish("more.created", b"kept")
    await ensure_stream(jetstream, older)
    info = await jetstream.stream_info("TEST")
    assert info.config.subjects == ["test.>", "more.>", "old.>"]
    assert info.config.duplicate_window == timedelta(minutes=5).total_seconds()
    assert info.config.max_age == timedelta(days=30).total_seconds()
    assert info.state.messages == 1


async def test_a_stream_that_never_expires_keeps_never_expiring(jetstream: JetStreamContext) -> None:
    """Someone made it keep its events for ever (a zero age): no spec of a service shortens that."""
    await created_by_someone_else(jetstream, StreamConfig(name="TEST", subjects=["test.>"], max_age=0))
    await ensure_stream(jetstream, STREAM)
    assert (await configuration(jetstream, "TEST")).max_age == 0


async def test_a_stream_that_holds_its_spec_already_is_not_written(jetstream: JetStreamContext) -> None:
    await ensure_stream(jetstream, STREAM)
    before = await configuration(jetstream, "TEST")
    counting = Counting(jetstream)
    await ensure_stream(counting, STREAM)
    await ensure_stream(counting, replace(STREAM, max_age=timedelta(days=1)))  # an older, shorter spec
    assert await configuration(jetstream, "TEST") == before
    assert counting.writes == 0


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


@final
class Counting(StreamApi):
    """JetStream, counting the streams it was asked to write."""

    def __init__(self, api: StreamApi) -> None:
        self._api = api
        self.writes = 0

    @override
    async def stream_info(self, name: str) -> StreamInfo:
        return await self._api.stream_info(name)

    @override
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        self.writes += 1
        return await self._api.add_stream(config)

    @override
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        self.writes += 1
        return await self._api.update_stream(config)


@final
class Answering(StreamApi):
    """JetStream that answers every write with ``error``, the stream existing or not."""

    def __init__(self, error: APIError, *, exists: bool) -> None:
        self._error = error
        self._exists = exists

    @override
    async def stream_info(self, name: str) -> StreamInfo:
        if not self._exists:
            raise NotFoundError
        return StreamInfo(
            config=StreamConfig(name=name, subjects=["other.>"]), state=StreamState(0, 0, 0, 0, 0)
        )

    @override
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        raise self._error

    @override
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        raise self._error


TRANSIENT: Final = {
    "no responders": ServiceUnavailableError(),
    "10008 temporarily unavailable": ServiceUnavailableError(code=503, err_code=10008),
    "10023 insufficient resources": ServiceUnavailableError(code=503, err_code=10023),
    "10009 no leader": ServerError(code=500, err_code=10009),
    "10058 created meanwhile with another configuration": BadRequestError(code=400, err_code=10058),
    "10069 update failed": ServerError(code=500, err_code=10069),
    "10202 member change in progress": BadRequestError(code=400, err_code=10202),
}
"""What JetStream answers while it cannot serve for now (nats-server 2.12.15, server/errors.json): the start
fails, and the next one tries again. 10058 is a replica of another version creating the stream at the same
moment: the next attempt finds it and updates it."""
REFUSALS: Final = {
    "10065 subjects overlap": BadRequestError(code=400, err_code=10065, description="subjects overlap"),
    "10052 invalid configuration": ServerError(code=500, err_code=10052, description="invalid"),
    "10027 too many streams": BadRequestError(code=400, err_code=10027, description="maximum streams"),
    "10002 account resources": BadRequestError(code=400, err_code=10002, description="resource limits"),
    "10113 max bytes required": BadRequestError(code=400, err_code=10113, description="max bytes"),
}
"""What JetStream answers to a stream configuration it will never accept (same source)."""


@pytest.mark.parametrize("exists", [True, False], ids=["on update", "on create"])
@pytest.mark.parametrize("answer", TRANSIENT.values(), ids=TRANSIENT.keys())
async def test_a_jetstream_that_cannot_serve_for_now_is_no_refusal_and_its_error_goes_on(
    answer: APIError, *, exists: bool
) -> None:
    with pytest.raises(APIError) as raised:
        await ensure_stream(Answering(answer, exists=exists), STREAM)
    assert raised.value is answer


@pytest.mark.parametrize("exists", [True, False], ids=["on update", "on create"])
@pytest.mark.parametrize("answer", REFUSALS.values(), ids=REFUSALS.keys())
async def test_a_configuration_jetstream_will_never_accept_is_a_refused_stream(
    answer: APIError, *, exists: bool
) -> None:
    with pytest.raises(StreamRefusedError, match="'TEST'") as refused:
        await ensure_stream(Answering(answer, exists=exists), STREAM)
    assert refused.value.__cause__ is answer


@final
class InMemory(StreamApi):
    """One stream's configuration, kept as JetStream keeps it."""

    def __init__(self) -> None:
        self.config: StreamConfig | None = None

    @override
    async def stream_info(self, name: str) -> StreamInfo:
        if self.config is None:
            raise NotFoundError
        return StreamInfo(config=self.config, state=StreamState(0, 0, 0, 0, 0))

    @override
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        self.config = config
        return StreamInfo(config=config, state=StreamState(0, 0, 0, 0, 0))

    @override
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        return await self.add_stream(config)


SUBJECTS: Final = ("a.>", "b.>", "c.>", "d.>")


def declared(subjects: list[str], window: int, beyond_the_window: int) -> StreamSpec:
    return StreamSpec(
        name="TEST",
        subjects=tuple(subjects),
        duplicate_window=timedelta(seconds=window),
        max_age=timedelta(seconds=window + beyond_the_window),
    )


specs = st.builds(
    declared,
    st.lists(st.sampled_from(SUBJECTS), min_size=1, max_size=4, unique=True),
    st.integers(min_value=1, max_value=600),
    st.integers(min_value=0, max_value=10_000),
)


@given(st.lists(specs, min_size=1, max_size=6))
async def test_whatever_order_the_specs_come_in_the_stream_holds_them_all(applied: list[StreamSpec]) -> None:
    """The law of a rollout: every subject any spec declared, and the longest of every limit."""
    stream = InMemory()
    for spec in applied:
        await ensure_stream(stream, spec)
    assert stream.config is not None
    assert set(stream.config.subjects or ()) == {subject for spec in applied for subject in spec.subjects}
    assert stream.config.max_age == max(spec.max_age for spec in applied).total_seconds()
    assert stream.config.duplicate_window == max(spec.duplicate_window for spec in applied).total_seconds()


@final
class Overwriting(StreamApi):
    """JetStream with a second replica that read the stream before every write of ours and writes after it.

    Its write is built from the stream as it was, so it lands over ours without ours: ``times`` writes of
    ours are overwritten this way.
    """

    def __init__(self, held: StreamConfig, *, times: int) -> None:
        self._stream = InMemory()
        self._stream.config = held
        self._stale = held
        self._times = times
        self.writes = 0

    @property
    def config(self) -> StreamConfig:
        assert self._stream.config is not None
        return self._stream.config

    def other_replica_declares(self, *subjects: str) -> None:
        self._stale = replace(self._stale, subjects=[*(self._stale.subjects or ()), *subjects])

    @override
    async def stream_info(self, name: str) -> StreamInfo:
        return await self._stream.stream_info(name)

    @override
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        raise AssertionError  # the stream exists

    @override
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        self.writes += 1
        info = await self._stream.update_stream(config)
        if self._times:
            self._times -= 1
            await self._stream.update_stream(self._stale)
        return info


async def test_a_write_overwritten_by_a_replica_that_read_before_it_is_made_again() -> None:
    """Two versions start at once, both read the stream, both write: the later write is built from the old
    stream and drops what the earlier added. Reading the stream back shows it, and the loser writes again."""
    stream = Overwriting(StreamConfig(name="TEST", subjects=["a.>"]), times=1)
    stream.other_replica_declares("b.>")
    await ensure_stream(stream, StreamSpec(name="TEST", subjects=("a.>", "c.>")))
    assert set(stream.config.subjects or ()) == {"a.>", "b.>", "c.>"}
    assert stream.writes == 2


async def test_a_stream_that_keeps_being_overwritten_is_given_up_on_after_a_few_writes() -> None:
    stream = Overwriting(StreamConfig(name="TEST", subjects=["a.>"]), times=MAX_ATTEMPTS + 5)
    with pytest.raises(StreamNotSettledError, match="'TEST'") as unsettled:
        await ensure_stream(stream, StreamSpec(name="TEST", subjects=("a.>", "c.>")))
    assert stream.writes == MAX_ATTEMPTS
    assert unsettled.value.code == "stream_not_settled"
    assert unsettled.value.kind is ErrorKind.UNAVAILABLE
