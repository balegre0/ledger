"""JetStream streams: declared by the services that publish to them, created or updated at start."""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import ClassVar, Final, Protocol, final

from nats.js.api import StorageType, StreamConfig
from nats.js.errors import APIError, NotFoundError
lazy from nats.js.api import StreamInfo

from ledger_kernel.errors import LedgerError, described


class InvalidStreamSpecError(LedgerError):
    """A stream declaration that NATS would read as something else. A mistake in the code, not input."""

    code: ClassVar[str] = "invalid_stream_spec"

    def __init__(self, *, field: str, requirement: str, value: object) -> None:
        super().__init__(f"stream {field} must be {requirement}, not {described(value)}")


class StreamRefusedError(LedgerError):
    """JetStream refused to create or update a stream, and left it as it was.

    Typically its subjects overlap with another stream's. The cause is the server's own answer, or nats-py's
    when it could not even build the request.
    """

    code: ClassVar[str] = "stream_refused"

    def __init__(self, name: str) -> None:
        super().__init__(f"JetStream refused the configuration of stream {name!r}")


@final
@dataclass(frozen=True, slots=True)
class StreamSpec:
    """A stream and the subjects it stores.

    JetStream fills in what a stream leaves out: one without subjects stores the subject of its own name, and
    a zero age never expires. Both are refused here instead of defaulted, and so is a window longer than the
    age, which JetStream refuses.
    """

    name: str
    subjects: tuple[str, ...]
    duplicate_window: timedelta = timedelta(minutes=2)
    """How long a repeated ``Nats-Msg-Id`` is recognized and dropped: the relay may publish an event twice.

    It must be longer than the gap between two attempts to publish the same event: at most the outbox
    relay's publish timeout, retry delay and idle wait at their ceilings, 20 + 30 + 30 = 80 seconds, or 100
    when a slow pass is in flight (the default of two minutes leaves room). A republish after an outage or a
    restart can come later than that; an event published again after the window is stored twice, and the
    consumer's inbox is the backstop.
    """
    max_age: timedelta = timedelta(days=7)
    """How long an event stays in the stream, for consumers that were down and for replays."""

    def __post_init__(self) -> None:
        if not self.subjects:
            raise InvalidStreamSpecError(
                field="subjects", requirement="at least one subject", value=self.subjects
            )
        for field, value in (("duplicate_window", self.duplicate_window), ("max_age", self.max_age)):
            if value <= timedelta(0):
                raise InvalidStreamSpecError(field=field, requirement="longer than zero", value=value)
        if self.duplicate_window > self.max_age:
            raise InvalidStreamSpecError(
                field="duplicate_window",
                requirement=f"at most max_age ({self.max_age})",
                value=self.duplicate_window,
            )


DEAD_LETTERS: Final = StreamSpec(
    name="DEAD_LETTERS",
    subjects=(
        "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.>",
        "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.>",
    ),
    max_age=timedelta(days=30),
)
"""JetStream's notices of events a consumer gave up on: each names the stream and sequence to inspect."""


class StreamApi(Protocol, metaclass=ABCMeta):
    """What the chassis asks of JetStream to manage streams: a ``JetStreamContext`` is one.

    nats-py leaves the ``**params`` of ``add_stream`` and ``update_stream`` without a type, and strict type
    checking refuses to call a method whose signature is partly unknown. Seen through this protocol, only
    the parameter the chassis passes remains.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def stream_info(self, name: str) -> StreamInfo:
        """The stream's configuration and state; ``NotFoundError`` when there is none."""

    @abstractmethod
    async def add_stream(self, config: StreamConfig) -> StreamInfo:
        """Create the stream ``config`` describes."""

    @abstractmethod
    async def update_stream(self, config: StreamConfig) -> StreamInfo:
        """Replace the configuration of the stream ``config`` names."""


async def ensure_stream(jetstream: StreamApi, spec: StreamSpec) -> None:
    """Create the stream with file storage, or bring an existing one's subjects and limits to ``spec``.

    Nothing else of an existing stream changes: its storage, retention and replicas are what whoever made it
    chose, and starting a service must never move data. ``StreamRefusedError`` when JetStream refuses, or
    nats-py cannot build the request.

    Specs only move forward. Replicas of different versions start during a rollout and each applies its own
    spec over what the others declared, so a new spec must keep everything an older one declares valid (more
    subjects, no shorter limits): otherwise an older replica restarting after a newer one shrinks the stream.
    """
    try:
        existing = await jetstream.stream_info(spec.name)
    except NotFoundError:
        apply, base = jetstream.add_stream, StreamConfig(name=spec.name, storage=StorageType.FILE)
    else:
        apply, base = jetstream.update_stream, existing.config
    config = replace(
        base,
        subjects=list(spec.subjects),
        duplicate_window=spec.duplicate_window.total_seconds(),
        max_age=spec.max_age.total_seconds(),
    )
    try:
        await apply(config)
    except (APIError, ValueError) as error:  # nats-py raises ValueError when it cannot serialize the config
        raise StreamRefusedError(spec.name) from error
