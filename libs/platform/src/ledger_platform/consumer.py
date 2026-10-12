"""Event consumers: each event handled once in effect, in its tenant's transaction.

A consumer claims ``(consumer, event id)`` in the ``inbox`` table in the same transaction as the handler's own
writes, so the claim and the effects commit together or not at all. An event JetStream delivers again finds
the claim and is acknowledged without calling the handler. The message is acknowledged only after the commit.

What happens to a delivery:

* handled or a duplicate: acknowledged;
* the handler raised: negatively acknowledged with a delay from the spec's ``backoff``, until JetStream has
  delivered it ``max_deliver`` times and gives up (a ``MAX_DELIVERIES`` advisory, stored by ``DEAD_LETTERS``);
* the envelope cannot be read, or the handler raised an error of kind ``INVALID`` (the event can never
  succeed): terminated at once (a ``MSG_TERMINATED`` advisory, stored by ``DEAD_LETTERS`` too).

An advisory names the stream and the sequence of the message to inspect; the log line of the failure names the
same sequence, with the event's id and type. The consumer's own log fields never include the payload or the
headers; the traceback of a handler's exception says whatever that exception says.

Long handlers. JetStream delivers a message again when nobody has acknowledged it within ``ack_wait``, so a
handler that takes longer is handed the same event a second time, here or on another replica, while the first
handling is still running. The inbox makes that safe: the second claim waits on the inbox's primary key until
the first transaction ends, then finds the event handled (a duplicate) or, if the first rolled back, handles
it itself. The wait is bounded by the database's ``lock_timeout`` (5 seconds by default): a claim that waits
longer fails like any handler error and is retried after its backoff, using up one of the ``max_deliver``
deliveries. A handler's database work is bounded by the statement timeout of the database (30 seconds by
default, for each statement): ``ack_wait`` defaults to the same 30 seconds, so a handler within its statement
limits is usually done before JetStream looks for it again.
"""

import contextlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import inf
from typing import ClassVar, Final, Never, final
from uuid import UUID
lazy from collections.abc import Awaitable, Callable

import structlog
from nats.errors import Error as NatsError
from nats.js.api import AckPolicy, ConsumerConfig
from prometheus_client import Counter
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column
lazy from nats.aio.msg import Msg
lazy from nats.js import JetStreamContext
lazy from prometheus_client import CollectorRegistry

from ledger_contracts.envelope import Envelope, MalformedEventError
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import ErrorKind, LedgerError, described
from ledger_platform.database import PlatformTable
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Database, UnitOfWork
lazy from ledger_platform.tracing import Tracing

DEFAULT_MAX_DELIVER: Final = 10
DEFAULT_BACKOFF: Final = (1.0, 10.0, 60.0, 300.0, 600.0)
"""Seconds to wait after the first failed delivery, after the second, and so on; the last one repeats."""
DEFAULT_ACK_WAIT: Final = 30.0
"""Seconds JetStream waits for an acknowledgement before it delivers the message again: the default
statement timeout of ``Database``."""
DEFAULT_BATCH: Final = 20
DEFAULT_POLL: Final = 5.0
MIN_POLL: Final = 0.001
"""Polling more often than this is a busy loop."""
_NAME: Final = re.compile(r"(?:(?![.*>/\\])[!-~])+", re.ASCII)
"""Visible ASCII without ``.``, ``*``, ``>``, ``/`` or a backslash: what NATS accepts as a consumer's name."""
_logger = structlog.stdlib.get_logger(__name__)

type Handler = Callable[[Envelope, UnitOfWork], Awaitable[None]]
"""Handles one event in ``unit``, the event's own transaction: the tenant is set, and what the handler writes
commits together with the event's claim in the inbox. Raise to have the delivery retried."""


class InvalidConsumerOptionError(LedgerError):
    """A consumer option that cannot work. A mistake in the code or the configuration, never client input."""

    code: ClassVar[str] = "invalid_consumer_option"

    def __init__(self, *, option: str, requirement: str, value: object) -> None:
        super().__init__(f"consumer option {option} must be {requirement}, not {described(value)}")


def _is_positive_and_finite(value: float) -> bool:
    return 0 < value < inf


def _require_name(name: str) -> None:
    if _NAME.fullmatch(name) is None:
        requirement = "one or more visible ASCII characters, none of '.', '*', '>', '/' or a backslash"
        raise InvalidConsumerOptionError(option="name", requirement=requirement, value=name)


def _require_backoff(backoff: tuple[float, ...], max_deliver: int) -> None:
    if not backoff:
        raise InvalidConsumerOptionError(option="backoff", requirement="at least one delay", value=backoff)
    if not all(_is_positive_and_finite(delay) for delay in backoff):
        raise InvalidConsumerOptionError(
            option="backoff", requirement="positive, finite numbers of seconds", value=backoff
        )
    if len(backoff) > max_deliver:
        raise InvalidConsumerOptionError(
            option="backoff", requirement=f"at most max_deliver ({max_deliver}) delays", value=backoff
        )


def _require_seconds(option: str, value: float, *, floor: float | None = None) -> None:
    if not _is_positive_and_finite(value):
        raise InvalidConsumerOptionError(
            option=option, requirement="a positive, finite number of seconds", value=value
        )
    if floor is not None and value < floor:
        raise InvalidConsumerOptionError(option=option, requirement=f"at least {floor} seconds", value=value)


def _read(message: Msg) -> Envelope | None:
    """The envelope ``message`` carries, or ``None`` when its headers or body cannot be read."""
    try:
        return Envelope.from_headers(message.headers or {}, message.data)
    except MalformedEventError:
        return None


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

    JetStream applies this configuration when it creates the durable consumer. One that already exists keeps
    the configuration it was created with: changing ``max_deliver`` or ``ack_wait`` of a deployed consumer
    takes effect only after deleting it (it then starts over with the whole stream, and the inbox makes every
    event it has handled before a duplicate).
    """

    name: str
    """The durable name in JetStream, the ``consumer`` of its inbox rows and of its metrics. Visible ASCII
    without ``.``, ``*``, ``>``, ``/`` or a backslash. A new name starts over with the whole stream and
    handles every event once more, as another consumer."""
    stream: str
    """The stream the consumer reads."""
    subject: str
    """Which of the stream's subjects it takes (a filter, wildcards allowed)."""
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
    handler is still working (see the module's note on long handlers). Longer than the handler usually takes,
    which the database's statement timeout bounds for each statement."""

    def __post_init__(self) -> None:
        _require_name(self.name)
        if self.max_deliver < 1:
            raise InvalidConsumerOptionError(
                option="max_deliver", requirement="at least 1", value=self.max_deliver
            )
        _require_backoff(self.backoff, self.max_deliver)
        _require_seconds("ack_wait", self.ack_wait)

    def retry_delay(self, delivery: int) -> float:
        """Seconds to wait after the ``delivery``-th delivery failed (the first is 1)."""
        return self.backoff[min(delivery, len(self.backoff)) - 1]


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

        Binds to the durable consumer, creating it from the spec if there is none. A handler's failure never
        ends the loop. What does: an error of NATS itself (the stream is gone, the connection is closed) and
        a cancellation; the service's supervision reports the first and restarts, and a message that was not
        acknowledged is delivered again after ``ack_wait``.

        The messages of a batch are handled one after the other, so ``batch`` times the usual time to handle
        one should stay well inside ``ack_wait``; otherwise the tail of a batch is delivered again while it
        waits (harmless, as the inbox shows, but wasted work).
        """
        if batch < 1:
            raise InvalidConsumerOptionError(option="batch", requirement="at least 1", value=batch)
        _require_seconds("poll", poll, floor=MIN_POLL)
        subscription = await jetstream.pull_subscribe(
            self._spec.subject,
            durable=self._spec.name,
            stream=self._spec.stream,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                max_deliver=self._spec.max_deliver,
                ack_wait=self._spec.ack_wait,
            ),
        )
        try:
            while True:
                try:
                    messages = await subscription.fetch(batch, timeout=poll)
                except TimeoutError:  # nats-py's timeouts are subclasses of the built-in one
                    continue
                for message in messages:
                    await self.handle(message)
        finally:
            # A pull request left at the server would be answered, and its message would wait for ``ack_wait``
            # in a subscription nobody reads. The connection may be the very thing that is gone.
            with contextlib.suppress(NatsError):
                await subscription.unsubscribe()

    async def handle(self, message: Msg) -> None:
        """Handle one delivery and settle it.

        Acknowledged after the commit, negatively acknowledged with the backoff's delay, or terminated. Only
        an error of NATS itself (the settlement could not be sent) is raised.
        """
        envelope = _read(message)
        if envelope is None:
            metadata = message.metadata
            _logger.error(
                "consumer.event_malformed",
                consumer=self._spec.name,
                subject=message.subject,
                delivery=metadata.num_delivered,
                stream_sequence=metadata.sequence.stream,
            )
            await self._terminate(message, Outcome.MALFORMED)
            return
        try:
            # The error leaves the span before it is handled here: a failed handling shows in the trace.
            with self._tracing.consuming(f"consume {self._spec.name}", envelope.traceparent):
                outcome = await self._claim_and_handle(envelope)
        except Exception as error:  # a handler may raise anything; a cancellation is no Exception
            fields = self._fields(message, envelope)
            if isinstance(error, LedgerError) and error.kind is ErrorKind.INVALID:
                _logger.exception("consumer.event_rejected", code=error.code, **fields)
                await self._terminate(message, Outcome.REJECTED)
            else:
                _logger.exception("consumer.handler_failed", **fields)
                await self._retry(message)
            return
        self._metrics.count(self._spec.name, outcome)
        await message.ack()

    async def _claim_and_handle(self, envelope: Envelope) -> Outcome:
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
