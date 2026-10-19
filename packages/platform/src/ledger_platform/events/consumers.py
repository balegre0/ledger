"""Consumers (§3.5): who handles which events, and the events as a handler receives them (Decision 16).

A module's assembly hands ``ConsumerSpec(name=…, handler=…)`` or ``ConsumerSpec(name=…, outside=…)``; its
manifest declares the same name with its event types, attempts, ordering and style (``ConsumerDecl``); the
runtime merges them into a ``Consumer`` the runner runs. Both receive a ``RawEvent`` (the envelope and the
JSON payload).

- An *inside* ``Handler`` also receives the unit it must write in: its effects and the deletion of its inbox
  row commit together (exactly once in effect). It may not call another module (rule 13).
- An *outside* handler (``OutsideHandler``, style ``outside``) runs with no unit open, so it may call other
  modules' facades; its row is deleted afterwards, in a unit of its own, under its lease. It runs at least
  once: what it calls is idempotent by the event (its id as the idempotency token, ``call_id`` of the
  ``SystemCall`` it builds) or by a version (rule 4).
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime
lazy from uuid import UUID

from pydantic import TypeAdapter

from ledger_kernel.errors import InvalidValueError, LedgerError
from ledger_platform.data.scopes import scope_of
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.config.manifest import ConsumerDecl, ConsumerStyle
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.data.unit_of_work import UnitOfWork

type Handler = Callable[[RawEvent, UnitOfWork], Awaitable[None]]
"""Handles one event inside the unit its inbox row is deleted in."""
type OutsideHandler = Callable[[RawEvent], Awaitable[None]]
"""Handles one event with no unit open (it may call facades); its inbox row goes after it, under its lease."""


class ConsumerMismatchError(LedgerError):
    """An assembly handed a consumer its manifest does not declare: a defect of the module."""

    code: ClassVar[str] = "consumer_mismatch"

    def __init__(self, spec: str, declared: str) -> None:
        super().__init__(f"consumer {spec} is not the declared {declared}")


class ConsumerStyleMismatchError(ConsumerMismatchError):
    """An assembly handed an outside handler for a consumer declared inside, or the other way round."""

    code: ClassVar[str] = "consumer_style_mismatch"

    def __init__(self, consumer: str, handed: ConsumerStyle, declared: ConsumerStyle) -> None:
        super().__init__(f"{consumer} ({handed})", f"{consumer} ({declared})")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RawEvent:
    """An event as stored: its envelope and its payload (JSON)."""

    id: UUID
    type: str
    version: int
    source: str
    scope: Scope | None
    subject: UUID | None
    aggregate_version: int
    occurred_at: datetime
    traceparent: str | None
    payload: Mapping[str, JsonValue]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _StoredEnvelope:
    """The envelope columns of an inbox row (its queue columns are ignored)."""

    event_id: UUID
    type: str
    version: int
    source: str
    organization_id: UUID | None
    company_id: UUID | None
    mode: str | None
    subject: UUID | None
    aggregate_version: int
    occurred_at: datetime
    traceparent: str | None
    payload: dict[str, JsonValue]


_ENVELOPE: Final = TypeAdapter(_StoredEnvelope)


def raw_event_of(row: Mapping[str, object]) -> RawEvent:
    """The event an inbox row holds (its scope through ``scope_of``)."""
    stored = _ENVELOPE.validate_python(dict(row))
    return RawEvent(
        id=stored.event_id,
        type=stored.type,
        version=stored.version,
        source=stored.source,
        scope=scope_of(stored.organization_id, stored.company_id, stored.mode),
        subject=stored.subject,
        aggregate_version=stored.aggregate_version,
        occurred_at=stored.occurred_at,
        traceparent=stored.traceparent,
        payload=stored.payload,
    )


@final
@dataclass(frozen=True, slots=True)
class Outside:
    """An outside consumer's handler, as a ``Consumer`` holds it (an inside one is held as it is)."""

    handler: OutsideHandler


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsumerSpec:
    """What an assembly hands: a declared consumer's name and exactly one handler (``InvalidValueError``).

    ``handler`` for an inside consumer, ``outside`` for an outside one (see the module).
    """

    name: str
    handler: Handler | None = None
    outside: OutsideHandler | None = None

    def __post_init__(self) -> None:
        _ = self.handling

    @property
    def handling(self) -> Handler | Outside:
        """The one handler, an outside one wrapped (``InvalidValueError`` with none or both)."""
        if self.outside is None:
            if self.handler is None:
                raise _one_handler(self.name)
            return self.handler
        if self.handler is not None:
            raise _one_handler(self.name)
        return Outside(self.outside)

    @property
    def style(self) -> ConsumerStyle:
        """Where its handler runs."""
        return "inside" if self.outside is None else "outside"


def _one_handler(name: str) -> InvalidValueError:
    return InvalidValueError(field=f"consumer {name}", requirement="given exactly one of handler and outside")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Consumer:
    """A consumer as the runner runs it: the declaration and the handler, merged."""

    name: str
    types: frozenset[str]
    max_attempts: int
    ordered: bool
    handler: Handler | Outside

    @property
    def outside(self) -> bool:
        """Whether its handler runs with no unit open (see the module)."""
        return isinstance(self.handler, Outside)

    @classmethod
    def of(cls, spec: ConsumerSpec, decl: ConsumerDecl) -> Consumer:
        """Merge an assembly's spec with its declaration (``ConsumerMismatchError`` if they differ).

        They differ in their name, or in their style (an outside handler for an inside declaration, or the
        other way round).
        """
        if spec.name != decl.consumer:
            raise ConsumerMismatchError(spec.name, decl.consumer)
        if spec.style != decl.style:
            raise ConsumerStyleMismatchError(spec.name, spec.style, decl.style)
        return cls(
            name=spec.name,
            types=frozenset(decl.types),
            max_attempts=decl.max_attempts,
            ordered=decl.ordered,
            handler=spec.handling,
        )
