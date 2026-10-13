"""Consumers (§3.5): who handles which events, and the events as a handler receives them (Decision 16).

A module's assembly hands ``ConsumerSpec(name, handler)``; its manifest declares the same name with its
event types, attempts and ordering (``ConsumerDecl``); the runtime merges them into a ``Consumer`` the runner
runs. A handler receives a ``RawEvent`` (the envelope and the JSON payload) and the unit it must write in: its
effects and the deletion of its inbox row commit together.
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from collections.abc import Awaitable, Callable, Mapping
lazy from datetime import datetime
lazy from uuid import UUID

from pydantic import TypeAdapter

from ledger_kernel.errors import LedgerError
from ledger_platform.data.scopes import scope_of
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.config.manifest import ConsumerDecl
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.data.unit_of_work import UnitOfWork

type Handler = Callable[[RawEvent, UnitOfWork], Awaitable[None]]
"""Handles one event inside the unit its inbox row is deleted in."""


class ConsumerMismatchError(LedgerError):
    """An assembly handed a consumer its manifest does not declare: a defect of the module."""

    code: ClassVar[str] = "consumer_mismatch"

    def __init__(self, spec: str, declared: str) -> None:
        super().__init__(f"consumer {spec} is not the declared {declared}")


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
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsumerSpec:
    """What an assembly hands: a declared consumer's name and its handler."""

    name: str
    handler: Handler


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Consumer:
    """A consumer as the runner runs it: the declaration and the handler, merged."""

    name: str
    types: frozenset[str]
    max_attempts: int
    ordered: bool
    handler: Handler

    @classmethod
    def of(cls, spec: ConsumerSpec, decl: ConsumerDecl) -> Consumer:
        """Merge an assembly's spec with its declaration (``ConsumerMismatchError`` if they differ)."""
        if spec.name != decl.consumer:
            raise ConsumerMismatchError(spec.name, decl.consumer)
        return cls(
            name=spec.name,
            types=frozenset(decl.types),
            max_attempts=decl.max_attempts,
            ordered=decl.ordered,
            handler=spec.handler,
        )
