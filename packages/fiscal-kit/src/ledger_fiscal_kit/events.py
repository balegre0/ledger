"""The kit's events (spec §8.4 ``events``): what every channel publishes, in names no channel owns.

A channel declares them in its manifest entry (``[[channels]] events.publishes``) and publishes them from its
own units; ``fiscal`` consumes them. Each is about a company in a mode (its scope: a moded ``Tenant``):

- ``DocumentUpdated`` (``fiscal_channel.document.updated``): a channel document changed state. Its payload is
  the ``StatusUpdate`` (``update``); its subject is the commercial document, its aggregate version the
  update's version (a consumer applies only newer ones).
- ``ReceivedDocumentRecorded`` (``fiscal_channel.received_document.recorded``): a document another taxpayer
  issued to the company was received and recorded; its subject is the received document.
- ``RequirementsChanged`` (``fiscal_channel.requirements.changed``): something the channel requires of the
  company changed (a range registered), so ``fiscal`` reviews its enrollments; its subject is the company.

fiscal reads the two it consumes back with their class's ``read``, from the ``RawEvent`` the platform hands
it: a raw event of another type or version, or a payload that is not the event's, is a defect of the routing
or of the producer (``InvalidValueError``).
"""

from dataclasses import dataclass
from typing import ClassVar, Final, final
lazy from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from ledger_fiscal_kit.channels import StatusUpdate
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.events import Event
from ledger_kernel.tenancy import Tenant
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.events.consumers import RawEvent

_UPDATE: Final = TypeAdapter(StatusUpdate)


def _require_moded_tenant(owner: str, scope: Scope | None) -> None:
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field=f"{owner}.scope", requirement="a company's tenant in a mode")


def _require_type(raw: RawEvent, kind: type[Event]) -> None:
    if (raw.type, raw.version) != (kind.TYPE, kind.VERSION):
        raise InvalidValueError(field="raw_event.type", requirement=f"{kind.TYPE} version {kind.VERSION}")


def _unreadable(kind: type[Event]) -> InvalidValueError:
    return InvalidValueError(field="raw_event.payload", requirement=f"a {kind.TYPE} payload")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentUpdated(Event):
    """A channel document changed state (see the module)."""

    TYPE: ClassVar[str] = "fiscal_channel.document.updated"
    VERSION: ClassVar[int] = 1
    update: StatusUpdate

    def __post_init__(self) -> None:
        _require_moded_tenant("document_updated", self.scope)
        if self.subject != self.update.document:
            raise InvalidValueError(field="document_updated.subject", requirement="the update's document")
        if self.aggregate_version != self.update.version:
            raise InvalidValueError(
                field="document_updated.aggregate_version", requirement="the update's version"
            )

    @classmethod
    def of(cls, scope: Tenant, update: StatusUpdate) -> DocumentUpdated:
        """``update`` of a document of ``scope``."""
        return cls(scope=scope, subject=update.document, aggregate_version=update.version, update=update)

    @classmethod
    def read(cls, raw: RawEvent) -> DocumentUpdated:
        """The event ``raw`` holds, as it was published."""
        _require_type(raw, cls)
        try:
            update = _UPDATE.validate_python(raw.payload.get("update"))
        except ValidationError as error:
            raise _unreadable(cls) from error
        return cls(
            scope=raw.scope, subject=raw.subject, aggregate_version=raw.aggregate_version, update=update
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceivedDocumentRecorded(Event):
    """A document issued to the company was received and recorded (see the module)."""

    TYPE: ClassVar[str] = "fiscal_channel.received_document.recorded"
    VERSION: ClassVar[int] = 1
    received: UUID
    """The received document's id in its channel."""
    channel: str
    environment: str
    """The authority's environment it came through: it implies the mode."""
    issuer: TaxId
    number: str
    """The number its issuer gave it for the authority (an e-NCF)."""

    def __post_init__(self) -> None:
        _require_moded_tenant("received_document_recorded", self.scope)
        if self.subject != self.received:
            raise InvalidValueError(
                field="received_document_recorded.subject", requirement="the received document"
            )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RequirementsChanged(Event):
    """What ``channel`` requires of the company changed: its enrollments are to be reviewed."""

    TYPE: ClassVar[str] = "fiscal_channel.requirements.changed"
    VERSION: ClassVar[int] = 1
    channel: str

    def __post_init__(self) -> None:
        scope = self.scope
        _require_moded_tenant("requirements_changed", scope)
        if not isinstance(scope, Tenant) or self.subject != scope.company:
            raise InvalidValueError(field="requirements_changed.subject", requirement="the tenant's company")

    @classmethod
    def of(cls, scope: Tenant, *, channel: str) -> RequirementsChanged:
        """``channel``'s requirements of ``scope``'s company changed."""
        return cls(scope=scope, subject=scope.company, aggregate_version=None, channel=channel)

    @classmethod
    def read(cls, raw: RawEvent) -> RequirementsChanged:
        """The event ``raw`` holds, as it was published (without an aggregate version: the outbox's 0)."""
        _require_type(raw, cls)
        channel = raw.payload.get("channel")
        if not isinstance(channel, str):
            raise _unreadable(cls)
        return cls(scope=raw.scope, subject=raw.subject, aggregate_version=None, channel=channel)
