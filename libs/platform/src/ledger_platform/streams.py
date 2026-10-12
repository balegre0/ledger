"""JetStream streams: declared by the services that publish to them, created or widened at start.

A stream only grows. Replicas of different versions start during a rollout, and each ensures its own spec over
what the others declared: an existing stream keeps every subject any of them declared and the longest of each
limit, so an older replica restarting after a newer one can never drop the newer subjects or have JetStream
delete events early. Shrinking a stream (a subject no version publishes any more, a shorter ``max_age``) is
an operations step, taken with the ``nats`` command line once no replica declares the old spec.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import ClassVar, Final, Protocol, final
lazy from collections.abc import Awaitable, Callable

from nats.js.api import StorageType, StreamConfig
from nats.js.errors import APIError, NotFoundError
lazy from nats.js.api import StreamInfo

from ledger_kernel.errors import LedgerError, UnavailableError
from ledger_platform.jetstream import is_refusal
from ledger_platform.options import InvalidOptionError

MAX_ATTEMPTS: Final = 4
"""How many times ``ensure_stream`` writes a stream before it gives up on it being settled."""


class InvalidStreamSpecError(InvalidOptionError):
    """A stream declaration that NATS would read as something else."""

    code: ClassVar[str] = "invalid_stream_spec"
    label: ClassVar[str] = "stream"


class StreamRefusedError(LedgerError):
    """JetStream refused the configuration of a stream for good, and left the stream as it was.

    Typically its subjects overlap with another stream's. Only an answer no retry can change is one
    (``ledger_platform.jetstream``): JetStream unable to serve for now raises its own error instead. The
    cause is the server's own answer, or nats-py's when it could not even build the request.
    """

    code: ClassVar[str] = "stream_refused"

    def __init__(self, name: str) -> None:
        super().__init__(f"JetStream refused the configuration of stream {name!r}")


class StreamNotSettledError(UnavailableError):
    """The stream kept losing what ``ensure_stream`` wrote: other replicas wrote it over and over."""

    code: ClassVar[str] = "stream_not_settled"

    def __init__(self, name: str) -> None:
        super().__init__(f"stream {name!r} was overwritten by others {MAX_ATTEMPTS} times in a row")


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
                option="subjects", requirement="at least one subject", value=self.subjects
            )
        for field, value in (("duplicate_window", self.duplicate_window), ("max_age", self.max_age)):
            if value <= timedelta(0):
                raise InvalidStreamSpecError(option=field, requirement="longer than zero", value=value)
        if self.duplicate_window > self.max_age:
            raise InvalidStreamSpecError(
                option="duplicate_window",
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


def _longest_age(held: float | None, declared: timedelta) -> float:
    """The longer of two ages in seconds: none (or zero, JetStream's word for it) is the longest of all."""
    return 0.0 if not held else max(held, declared.total_seconds())


def _created(spec: StreamSpec) -> StreamConfig:
    return StreamConfig(
        name=spec.name,
        storage=StorageType.FILE,
        subjects=list(spec.subjects),
        duplicate_window=spec.duplicate_window.total_seconds(),
        max_age=spec.max_age.total_seconds(),
    )


def _widened(held: StreamConfig, spec: StreamSpec) -> StreamConfig:
    """``held`` with every subject of ``spec`` added and each limit at the longer of the two."""
    subjects = list(held.subjects or ())
    return replace(
        held,
        subjects=subjects + [subject for subject in spec.subjects if subject not in subjects],
        duplicate_window=max(held.duplicate_window or 0.0, spec.duplicate_window.total_seconds()),
        max_age=_longest_age(held.max_age, spec.max_age),
    )


async def ensure_stream(jetstream: StreamApi, spec: StreamSpec) -> None:
    """Create the stream with file storage, or widen an existing one to hold ``spec`` too (see the module).

    An existing stream gains the subjects of ``spec`` it lacks, and the longer of its own limits and those of
    ``spec``; one that holds them all already is not written. Nothing else of it changes: its storage,
    retention and replicas are what whoever made it chose, and starting a service must never move data.

    JetStream updates a stream without asking what it was before, so replicas of different versions that
    start together can each write a configuration built from the stream as it was, the later one dropping
    what the earlier added. Each write is therefore followed by reading the stream back, and repeated while
    the stream lacks something of ``spec``: after at most ``MAX_ATTEMPTS`` writes, ``StreamNotSettledError``.
    Whatever the other replica lost, it finds out the same way when it reads its own stream back.

    ``StreamRefusedError`` when JetStream refuses the configuration for good, or nats-py cannot build the
    request. Any other answer (JetStream unable to serve for now) is raised as it came: it may well work next
    time.
    """
    for _ in range(MAX_ATTEMPTS):
        try:
            existing = await jetstream.stream_info(spec.name)
        except NotFoundError:
            apply, config = jetstream.add_stream, _created(spec)
        else:
            apply, config = jetstream.update_stream, _widened(existing.config, spec)
            if config == existing.config:
                return
        await _write(apply, config, spec.name)
    raise StreamNotSettledError(spec.name)


async def _write(
    apply: Callable[[StreamConfig], Awaitable[StreamInfo]], config: StreamConfig, name: str
) -> None:
    """Send ``config``; a refusal that no retry changes is a ``StreamRefusedError``."""
    try:
        await apply(config)
    except ValueError as error:  # nats-py raises it when it cannot serialize the config
        raise StreamRefusedError(name) from error
    except APIError as error:
        if not is_refusal(error):
            raise
        raise StreamRefusedError(name) from error
