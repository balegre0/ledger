"""Event consumers: each event handled once in effect, in its tenant's transaction.

A consumer claims ``(consumer, event id)`` in the ``inbox`` table in the same transaction as the handler's own
writes, so the claim and the effects commit together or not at all. An event JetStream delivers again finds
the claim and is acknowledged without calling the handler. The message is acknowledged only after the commit.

What happens to a delivery:

* handled or a duplicate: acknowledged;
* the claim or the handler failed: negatively acknowledged with a delay from the spec's ``backoff``, until
  JetStream has delivered it ``max_deliver`` times and gives up (a ``MAX_DELIVERIES`` advisory, stored by
  ``DEAD_LETTERS``);
* the envelope cannot be read, or the handler raised an error of kind ``INVALID`` (the event can never
  succeed): terminated at once (a ``MSG_TERMINATED`` advisory, stored by ``DEAD_LETTERS`` too).

An advisory names the stream and the sequence of the message to inspect; the log line of the failure names the
same sequence, with the event's id and type. The consumer's own log fields never include the payload or the
headers (an unreadable message is logged with the name of the header that failed, never its value); the
traceback of a handler's exception says whatever that exception says. A failure before the handler ran (the
database could not be reached, the claim waited too long for a lock) is logged as ``consumer.claim_failed``,
one of the handler as ``consumer.handler_failed``.

The spec is the configuration: ``Consumer.run`` creates the durable consumer in JetStream if it is missing
and brings an existing one to the spec, as ``ensure_stream`` does for a stream. ``max_deliver`` and
``ack_wait`` change in place, and that is safe while messages are pending: JetStream keeps the consumer's
state (its position, the messages awaiting an acknowledgement, their delivery counts).

The subject does not change in place. One durable consumer serves every replica of a service, and during a
rollout the replicas run different versions. Widening the subject would hand the events of a new kind to old
replicas that may not read them (their handler raises an error of kind ``INVALID``: the events are terminated
into the dead letters and the new version never sees them); narrowing it, as an old replica restarting after a
new one does, would skip the events of the new kind for good. So a changed subject is refused with a
``ConsumerRefusedError``, without a word to JetStream, as is a changed ack policy (JetStream never changes
one), and a service that needs a new subject gives its consumer a new name: a versioned one, such as
``invoicing-projections-v2``. The new consumer starts with the whole stream (its inbox rows are its own), and
the old one is deleted when no replica of the old version runs any more.

A stream that does not exist (yet) is no configuration mistake: whoever publishes to it creates it when it
starts, maybe a moment later. The consumer logs ``consumer.stream_missing`` and fails like any NATS error; the
service's supervisor runs it again after a growing delay, and it binds once the stream is there.

A consumer that is stopped (a deploy, a scale-down) rolls back the handling it was in, and gives the
messages of its batch it had not settled back to JetStream at once, so that the next consumer does not wait
for ``ack_wait`` to find them.

Long handlers. JetStream delivers a message again when nobody has acknowledged it within ``ack_wait``, so a
handler that takes longer is handed the same event a second time, here or on another replica, while the first
handling is still running. The inbox makes that safe: the second claim waits on the inbox's primary key until
the first transaction ends, then finds the event handled (a duplicate) or, if the first rolled back, handles
it itself. The wait is bounded by the database's ``lock_timeout`` (5 seconds by default): a claim that waits
longer fails like any other and is retried after its backoff, using up one of the ``max_deliver`` deliveries.
``ack_wait`` defaults to twice what one claim and one statement can take at the database's default limits
(``lock_timeout`` plus ``statement_timeout``). That is not a bound on a handler: it runs as many statements as
it likes, each within the statement timeout, and so may outlast ``ack_wait``. Then the event is delivered
again while the handler still works, and the inbox makes that safe, as above.
"""

import contextlib
import re
from abc import ABCMeta, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final, Never, Protocol, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable, Mapping

import structlog
from nats.errors import Error as NatsError
from nats.js.api import AckPolicy, ConsumerConfig
from nats.js.errors import APIError, NotFoundError
from prometheus_client import Counter
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column
lazy from nats.aio.msg import Msg
lazy from nats.js import JetStreamContext
lazy from nats.js.api import ConsumerInfo
lazy from prometheus_client import CollectorRegistry

from ledger_contracts.envelope import Envelope, MalformedEventError
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import ErrorKind, LedgerError
from ledger_platform.database import DEFAULT_LIMITS, PlatformTable
from ledger_platform.jetstream import is_refusal
from ledger_platform.options import (
    InvalidOptionError,
    is_positive_and_finite,
    require_at_least,
    require_seconds,
)
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.tracing import Tracing

DEFAULT_MAX_DELIVER: Final = 10
DEFAULT_BACKOFF: Final = (1.0, 10.0, 60.0, 300.0, 600.0)
"""Seconds to wait after the first failed delivery, after the second, and so on; the last one repeats."""
DEFAULT_ACK_WAIT: Final = 2 * (DEFAULT_LIMITS.lock_timeout + DEFAULT_LIMITS.statement_timeout).total_seconds()
"""Seconds JetStream waits for an acknowledgement before it delivers the message again: twice what a claim (it
may wait ``lock_timeout`` for a lock) and a statement (``statement_timeout``) can take at the default limits
of ``Database``. See the module's note on long handlers for what this is not."""
DEFAULT_BATCH: Final = 20
DEFAULT_POLL: Final = 5.0
MIN_POLL: Final = 0.001
"""Polling more often than this is a busy loop."""
MAX_NAME_LENGTH: Final = 255
"""The longest name NATS accepts for a consumer."""
_NAME: Final = re.compile(r"(?:(?![.*>/\\])[!-~])+", re.ASCII)
"""Visible ASCII without ``.``, ``*``, ``>``, ``/`` or a backslash: what NATS accepts as a consumer's name."""
_UNCHANGEABLE: Final[Mapping[str, str]] = {
    "ack_policy": "JetStream never changes the ack policy of a consumer",
    "filter_subject": (
        "one durable consumer serves every replica, whichever version it runs, so its subject cannot change"
        " in place"
    ),
}
"""The fields of an existing consumer the chassis refuses to change, and why, without asking JetStream."""
_STREAM_NOT_FOUND: Final = 10059
"""JetStream's ``err_code`` for a stream that does not exist (JSStreamNotFoundErr)."""
_logger = structlog.stdlib.get_logger(__name__)

type Handler = Callable[[Envelope, UnitOfWork], Awaitable[None]]
"""Handles one event in ``unit``, the event's own transaction: the tenant is set, and what the handler writes
commits together with the event's claim in the inbox. Raise to have the delivery retried."""


class InvalidConsumerOptionError(InvalidOptionError):
    """A consumer option that cannot work."""

    code: ClassVar[str] = "invalid_consumer_option"
    label: ClassVar[str] = "consumer option"


def _require_name(name: str) -> None:
    if len(name) > MAX_NAME_LENGTH or _NAME.fullmatch(name) is None:
        requirement = (
            f"1 to {MAX_NAME_LENGTH} visible ASCII characters, none of '.', '*', '>', '/' or a backslash"
        )
        raise InvalidConsumerOptionError(option="name", requirement=requirement, value=name)


def _require_backoff(backoff: tuple[float, ...], max_deliver: int) -> None:
    if not backoff:
        raise InvalidConsumerOptionError(option="backoff", requirement="at least one delay", value=backoff)
    if not all(is_positive_and_finite(delay) for delay in backoff):
        raise InvalidConsumerOptionError(
            option="backoff", requirement="positive, finite numbers of seconds", value=backoff
        )
    if len(backoff) > max_deliver:
        raise InvalidConsumerOptionError(
            option="backoff", requirement=f"at most max_deliver ({max_deliver}) delays", value=backoff
        )


def _read(message: Msg) -> Envelope | MalformedEventError:
    """The envelope ``message`` carries, or what is wrong with its headers."""
    try:
        return Envelope.from_headers(message.headers or {}, message.data)
    except MalformedEventError as error:
        return error


@final
@dataclass(slots=True)
class _Progress:
    """How far the handling of one delivery got, so that a failure can be told by where it happened."""

    claimed: bool = False
    """The inbox holds the event and the handler is about to run (or has)."""


class InboxRecord(PlatformTable):
    """An event a consumer has handled: a second delivery finds it and does nothing.

    Outside row-level security on purpose, like the outbox: it holds no business data, and the claim is made
    before the handler runs. The handler's own tables are protected as usual. Rows are never deleted in this
    milestone; a retention job comes with the first service that produces volume.
    """

    __tablename__: str = "inbox"

    consumer: Mapped[str] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Outcome(StrEnum):
    """What became of a delivery; the ``outcome`` label of ``ledger_events_total``."""

    HANDLED = "handled"
    DUPLICATE = "duplicate"
    """The inbox already held the event: acknowledged without calling the handler."""
    FAILED = "failed"
    """The handler raised, or the commit did not happen: retried after the backoff."""
    REJECTED = "rejected"
    """The handler raised an error of kind ``INVALID``: terminated, never retried."""
    MALFORMED = "malformed"
    """The envelope could not be read: terminated, never retried."""


@final
class ConsumerMetrics:
    """The counter ``ledger_events_total{consumer, outcome}``, one per registry and shared by its consumers.

    A registry refuses a second counter of the same name, so a service builds one ``ConsumerMetrics`` and
    hands it to every ``Consumer``; the ``consumer`` label tells them apart.
    """

    __slots__ = ("_events",)

    def __init__(self, registry: CollectorRegistry) -> None:
        self._events = Counter(
            "ledger_events",
            "Event deliveries consumed, by consumer and outcome.",
            ["consumer", "outcome"],
            registry=registry,
        )

    def track(self, consumer: str) -> None:
        """Export every outcome of ``consumer`` from now on, at zero.

        A series that appears with its first event hides that event from ``rate()`` and ``increase()``.
        """
        for outcome in Outcome:
            self._events.labels(consumer, outcome)

    def count(self, consumer: str, outcome: Outcome) -> None:
        """One more delivery of ``consumer`` ended in ``outcome``."""
        self._events.labels(consumer, outcome).inc()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsumerSpec:
    """A durable JetStream consumer and what it does with each event.

    ``Consumer.run`` brings the consumer in JetStream to this spec when it starts (``ensure_consumer``), so a
    new version of a service that changes ``max_deliver`` or ``ack_wait`` takes effect on the consumer it
    finds. ``backoff`` is not part of the consumer in JetStream: each negative acknowledgement carries its own
    delay, so it always is the running version's. The ``subject`` and the ``name`` go together: a consumer
    that exists under a name keeps its subject, and a new subject needs a new name.
    """

    name: str
    """The durable name in JetStream, the ``consumer`` of its inbox rows and of its metrics. Up to
    ``MAX_NAME_LENGTH`` visible ASCII characters, without ``.``, ``*``, ``>``, ``/`` or a backslash. A new
    name starts over with the whole stream and handles every event once more, as another consumer."""
    stream: str
    """The stream the consumer reads."""
    subject: str
    """Which of the stream's subjects it takes (a filter, wildcards allowed). It cannot change under the same
    ``name``: ``ensure_consumer`` refuses, because one durable consumer serves all the replicas of a service,
    old versions included. A service that needs another subject names its consumer anew, such as
    ``invoicing-projections-v2``; the new one starts with the whole stream."""
    handler: Handler
    """What to do with each event that is not a duplicate. An error it raises fails the delivery."""
    max_deliver: int = DEFAULT_MAX_DELIVER
    """How many times JetStream delivers an event before it gives up and publishes the ``MAX_DELIVERIES``
    advisory. At least 1: JetStream reads ``-1`` as unlimited, and an event that keeps failing would never
    reach the dead letters."""
    backoff: tuple[float, ...] = DEFAULT_BACKOFF
    """Seconds a failed delivery waits before the next one: the first entry after the first failure, the
    second after the second, and the last one for every failure after that. At least one entry and at most
    ``max_deliver``, positive and finite."""
    ack_wait: float = DEFAULT_ACK_WAIT
    """Seconds JetStream waits for an acknowledgement before delivering the message again, even though the
    handler is still working (see the module's note on long handlers). The default outlasts a claim and a
    statement at the database's default limits; a service with longer limits, or handlers that run many
    statements, raises it."""

    def __post_init__(self) -> None:
        _require_name(self.name)
        require_at_least(InvalidConsumerOptionError, "max_deliver", self.max_deliver)
        _require_backoff(self.backoff, self.max_deliver)
        require_seconds(InvalidConsumerOptionError, "ack_wait", self.ack_wait)

    def retry_delay(self, delivery: int) -> float:
        """Seconds to wait after the ``delivery``-th delivery failed (the first is 1)."""
        return self.backoff[min(delivery, len(self.backoff)) - 1]


class ConsumerRefusedError(LedgerError):
    """JetStream refused the configuration of a durable consumer for good, and left the consumer as it was.

    Or the chassis refused before asking: the subject and the ack policy of an existing consumer never
    change (see ``ensure_consumer``). Only an answer no retry can change is a refusal (see
    ``ledger_platform.jetstream``): JetStream unable to serve for now (no responders, no leader, no such
    stream yet) raises its own error instead. ``fields`` are the fields of the consumer that were to change;
    none when the consumer was to be created. The cause, when JetStream was asked, is the server's own
    answer, or nats-py's when it could not build the request.
    """

    code: ClassVar[str] = "consumer_refused"

    consumer: str
    stream: str
    fields: tuple[str, ...]

    def __init__(self, *, consumer: str, stream: str, fields: tuple[str, ...], reason: str | None) -> None:
        what = f"change {', '.join(fields)}" if fields else "be created"
        why = "" if reason is None else f": {reason}"
        super().__init__(f"consumer {consumer!r} of stream {stream!r} cannot {what}{why}")
        self.consumer = consumer
        self.stream = stream
        self.fields = fields


class ConsumerApi(Protocol, metaclass=ABCMeta):
    """What the chassis asks of JetStream to manage consumers: a ``JetStreamContext`` is one.

    nats-py leaves the ``**params`` of ``add_consumer`` without a type, and strict type checking refuses to
    call a method whose signature is partly unknown. Seen through this protocol, only the parameter the
    chassis passes remains.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def consumer_info(self, stream: str, consumer: str) -> ConsumerInfo:
        """The consumer's configuration and state; ``NotFoundError`` when there is none."""

    @abstractmethod
    async def add_consumer(self, stream: str, config: ConsumerConfig) -> ConsumerInfo:
        """Create the durable consumer ``config`` describes, or bring the one of that name to it."""


def _configuration(spec: ConsumerSpec) -> ConsumerConfig:
    return ConsumerConfig(
        name=spec.name,
        durable_name=spec.name,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=spec.ack_wait,
        max_deliver=spec.max_deliver,
        filter_subject=spec.subject,
    )


def _changes(current: ConsumerConfig, wanted: ConsumerConfig) -> tuple[str, ...]:
    """The fields ``wanted`` sets that ``current`` does not have as they are."""
    held = current.as_dict()
    return tuple(field for field, value in wanted.as_dict().items() if held.get(field) != value)


async def ensure_consumer(jetstream: ConsumerApi, spec: ConsumerSpec) -> None:
    """Create the durable consumer ``spec`` describes, or bring an existing one to it.

    A consumer that is already as the spec says is not written. ``max_deliver`` and ``ack_wait`` of an
    existing one are updated in place, which is safe while messages are pending (JetStream keeps the state of
    the consumer). Its subject and its ack policy are not: ``ConsumerRefusedError`` naming them, without a
    word to JetStream, because a durable consumer serves every replica whichever version it runs (see the
    module's note) and JetStream never changes an ack policy: a new subject needs a new name, such as
    ``invoicing-projections-v2``. ``ConsumerRefusedError`` too when JetStream refuses the configuration for
    good (``ledger_platform.jetstream``), or nats-py cannot build the request. Any other answer of JetStream
    (unavailable, no leader, no such stream yet) is raised as it came: it may well work next time. A missing
    stream is logged as ``consumer.stream_missing`` first.

    Specs of the fields that change in place only move forward, as streams do: replicas of different versions
    start during a rollout and each applies its own spec over what the others declared, so an older replica
    restarting after a newer one undoes the change until it is replaced too.
    """
    wanted = _configuration(spec)
    try:
        current = (await jetstream.consumer_info(spec.stream, spec.name)).config
    except NotFoundError:
        fields: tuple[str, ...] = ()
    else:
        fields = _changes(current, wanted)
        unchangeable = tuple(field for field in fields if field in _UNCHANGEABLE)
        if unchangeable:
            why = "; ".join(_UNCHANGEABLE[field] for field in unchangeable)
            raise ConsumerRefusedError(
                consumer=spec.name,
                stream=spec.stream,
                fields=unchangeable,
                reason=f"{why}: give the consumer a new name, such as '{spec.name}-v2'",
            )
        if not fields:
            return
    try:
        await jetstream.add_consumer(spec.stream, wanted)
    except ValueError as error:  # nats-py raises it when it cannot serialize the config
        raise ConsumerRefusedError(
            consumer=spec.name, stream=spec.stream, fields=fields, reason=None
        ) from error
    except APIError as error:
        if error.err_code == _STREAM_NOT_FOUND:
            _logger.warning("consumer.stream_missing", consumer=spec.name, stream=spec.stream)
        if not is_refusal(error):
            raise
        raise ConsumerRefusedError(
            consumer=spec.name, stream=spec.stream, fields=fields, reason=error.description
        ) from error


@final
class Consumer:
    """Pulls the events of one ``ConsumerSpec`` and handles each once in effect.

    Several replicas may run the same spec: JetStream shares the messages between them, and the inbox keeps
    an event delivered to two of them from being handled twice.
    """

    __slots__ = ("_clock", "_database", "_metrics", "_spec", "_tracing")

    def __init__(
        self,
        spec: ConsumerSpec,
        *,
        database: Database,
        tracing: Tracing,
        metrics: ConsumerMetrics,
        clock: Clock = utc_now,
    ) -> None:
        self._spec = spec
        self._database = database
        self._tracing = tracing
        self._metrics = metrics
        self._clock = clock
        metrics.track(spec.name)

    async def run(
        self, jetstream: JetStreamContext, *, batch: int = DEFAULT_BATCH, poll: float = DEFAULT_POLL
    ) -> Never:
        """Consume forever, up to ``batch`` messages at a time, waiting up to ``poll`` seconds for them.

        First brings the durable consumer to the spec (``ensure_consumer``: ``ConsumerRefusedError`` when
        JetStream refuses). A handler's failure never ends the loop. What does: an error of NATS itself (a
        request that timed out during an outage, the stream gone, the connection closed) and a cancellation.
        The service's supervisor (``ledger_platform.supervision``) logs such an error and runs the consumer
        again after a growing delay, binding it anew; a ``ConsumerRefusedError`` is a configuration mistake no
        new run can fix, so the consumer stays stopped and the process reports itself not live, to be
        restarted. On either, the handling in progress is rolled back and every message of the batch
        not yet settled goes back to JetStream at once (a negative acknowledgement without a delay: it counts
        as a delivery), so that nothing waits for ``ack_wait``; if the connection is gone, those messages are
        delivered again after ``ack_wait``.

        The messages of a batch are handled one after the other, so ``batch`` times the usual time to handle
        one should stay well inside ``ack_wait``; otherwise the tail of a batch is delivered again while it
        waits (harmless, as the inbox shows, but wasted work).
        """
        require_at_least(InvalidConsumerOptionError, "batch", batch)
        require_seconds(InvalidConsumerOptionError, "poll", poll, floor=MIN_POLL)
        await ensure_consumer(jetstream, self._spec)
        subscription = await jetstream.pull_subscribe_bind(durable=self._spec.name, stream=self._spec.stream)
        unsettled: deque[Msg] = deque()
        try:
            while True:
                try:
                    unsettled.extend(await subscription.fetch(batch, timeout=poll))
                except TimeoutError:  # nats-py's timeouts are subclasses of the built-in one
                    continue
                while unsettled:
                    await self.handle(unsettled[0])
                    unsettled.popleft()
        finally:
            # The connection may be what is gone: a settlement that fails is not worth a second error.
            for message in unsettled:
                with contextlib.suppress(NatsError):
                    await message.nak()
            # A pull request left at the server would be answered, and its message would wait for ``ack_wait``
            # in a subscription nobody reads.
            with contextlib.suppress(NatsError):
                await subscription.unsubscribe()

    async def handle(self, message: Msg) -> None:
        """Handle one delivery and settle it.

        Acknowledged after the commit, negatively acknowledged with the backoff's delay, or terminated. Only
        an error of NATS itself (the settlement could not be sent) is raised.
        """
        envelope = _read(message)
        if isinstance(envelope, MalformedEventError):
            metadata = message.metadata
            _logger.error(
                "consumer.event_malformed",
                consumer=self._spec.name,
                subject=message.subject,
                field=envelope.field,
                delivery=metadata.num_delivered,
                stream_sequence=metadata.sequence.stream,
            )
            await self._terminate(message, Outcome.MALFORMED)
            return
        progress = _Progress()
        try:
            # The error leaves the span before it is handled here: a failed handling shows in the trace.
            with self._tracing.consuming(f"consume {self._spec.name}", envelope.traceparent):
                outcome = await self._claim_and_handle(envelope, progress)
        except Exception as error:  # a handler may raise anything; a cancellation is no Exception
            fields = self._fields(message, envelope)
            if isinstance(error, LedgerError) and error.kind is ErrorKind.INVALID:
                _logger.exception("consumer.event_rejected", code=error.code, **fields)
                await self._terminate(message, Outcome.REJECTED)
            else:
                failed = "consumer.handler_failed" if progress.claimed else "consumer.claim_failed"
                _logger.exception(failed, **fields)
                await self._retry(message)
            return
        self._metrics.count(self._spec.name, outcome)
        await message.ack()

    async def _claim_and_handle(self, envelope: Envelope, progress: _Progress) -> Outcome:
        """The inbox claim and the handler in one transaction scoped to the event's tenant."""
        async with self._database.transaction(envelope.tenant) as unit:
            claimed = await unit.connection.execute(
                insert(InboxRecord)
                .values(consumer=self._spec.name, event_id=envelope.id, processed_at=self._clock())
                .on_conflict_do_nothing()
                .returning(InboxRecord.event_id)
            )
            if claimed.first() is None:
                return Outcome.DUPLICATE
            progress.claimed = True
            await self._spec.handler(envelope, unit)
        return Outcome.HANDLED

    def _fields(self, message: Msg, envelope: Envelope) -> dict[str, str | int]:
        """What a log line says about a delivery: enough to find it, never what it carries."""
        metadata = message.metadata
        return {
            "consumer": self._spec.name,
            "event_id": str(envelope.id),
            "type": envelope.type,
            "delivery": metadata.num_delivered,
            "stream_sequence": metadata.sequence.stream,
        }

    async def _retry(self, message: Msg) -> None:
        self._metrics.count(self._spec.name, Outcome.FAILED)
        await message.nak(delay=self._spec.retry_delay(message.metadata.num_delivered))

    async def _terminate(self, message: Msg, outcome: Outcome) -> None:
        self._metrics.count(self._spec.name, outcome)
        await message.term()
