"""The envelope of every integration event: CloudEvents 1.0 attributes carried as NATS headers.

The payload is a protobuf message; ``type`` is its full name (``ledger.events.invoicing.v1.DocumentIssued``),
so a consumer knows what it received before decoding. ``Nats-Msg-Id`` is the event id: JetStream drops a
duplicate published within its de-duplication window.

An ``Envelope`` that exists can be published and read back: whichever way it was made (``wrap``,
``from_headers``, the constructor or ``dataclasses.replace``), its invariants hold, and a violation names the
header that would carry the field. Every header value it produces is visible ASCII without spaces (``type``
and ``source`` are checked, the rest is formatted from typed values), so none can inject a header, break
the NATS header framing or be altered by header parsing.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final, final
lazy import builtins
lazy from collections.abc import Mapping
lazy from uuid import UUID

from google.protobuf.message import DecodeError
lazy from google.protobuf.message import Message

from ledger_kernel.clock import utc_now
from ledger_kernel.errors import InvalidInputError, excerpt
from ledger_kernel.ids import CompanyId, InvalidIdError, OrganizationId, Tenant, new_id, parse_id
lazy from ledger_kernel.clock import Clock

SPEC_VERSION: Final = "1.0"
CONTENT_TYPE: Final = "application/protobuf"
MAX_ATTRIBUTE_LENGTH: Final = 128
"""The longest ``type`` or ``source``: they are echoed, indexed and logged, so they stay short."""
_SPECVERSION_HEADER: Final = "ce-specversion"
_ID_HEADER: Final = "ce-id"
_TYPE_HEADER: Final = "ce-type"
_SOURCE_HEADER: Final = "ce-source"
_TIME_HEADER: Final = "ce-time"
_ORGANIZATION_HEADER: Final = "ce-organizationid"
_COMPANY_HEADER: Final = "ce-subject"
_TRACEPARENT_HEADER: Final = "traceparent"
_CONTENT_TYPE_HEADER: Final = "content-type"
_MESSAGE_ID_HEADER: Final = "Nats-Msg-Id"
_DATA_FIELD: Final = "data"
"""How an error names the payload, which travels as the message body, not in a header."""
_IDENTIFIER: Final = r"[A-Za-z_][A-Za-z0-9_]*"
_FULL_NAME: Final = re.compile(rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})+", re.ASCII)
"""A protobuf full name: a package of at least one segment, then the message name."""
_TOKEN: Final = re.compile(r"[!-~]+", re.ASCII)
"""Visible ASCII, no space."""
_TRACEPARENT: Final = re.compile(r"00-(?!0{32}-)[0-9a-f]{32}-(?!0{16}-)[0-9a-f]{16}-[0-9a-f]{2}", re.ASCII)
"""W3C trace context, version 00: lowercase hex, and neither the trace id nor the parent id all zeros."""


class MalformedEventError(InvalidInputError):
    """An event whose envelope or payload cannot be read: retrying cannot fix it."""

    code: ClassVar[str] = "malformed_event"

    def __init__(self, field: str, value: str = "") -> None:
        super().__init__(f"event field {field!r} is missing or invalid: {excerpt(value)}")


class UnexpectedEventError(InvalidInputError):
    """An event of another type than the consumer expects."""

    code: ClassVar[str] = "unexpected_event"

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"expected {expected}, got {excerpt(actual)}")


def _required(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "")
    if not value:
        raise MalformedEventError(name)
    return value


def _require_exactly(headers: Mapping[str, str], name: str, expected: str) -> None:
    value = _required(headers, name)
    if value != expected:
        raise MalformedEventError(name, value)


def _id(headers: Mapping[str, str], name: str) -> UUID:
    value = _required(headers, name)
    try:
        return parse_id(value)
    except InvalidIdError as error:
        raise MalformedEventError(name, value) from error


def _time(headers: Mapping[str, str]) -> datetime:
    value = _required(headers, _TIME_HEADER)
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise MalformedEventError(_TIME_HEADER, value) from error


def _tenant(headers: Mapping[str, str]) -> Tenant | None:
    if _ORGANIZATION_HEADER not in headers and _COMPANY_HEADER not in headers:
        return None
    organization = OrganizationId(_id(headers, _ORGANIZATION_HEADER))
    return Tenant(organization, CompanyId(_id(headers, _COMPANY_HEADER)))


def _is_attribute(pattern: re.Pattern[str], value: str) -> bool:
    """Whether ``value`` has the form ``pattern`` and fits in ``MAX_ATTRIBUTE_LENGTH`` (checked first)."""
    return len(value) <= MAX_ATTRIBUTE_LENGTH and pattern.fullmatch(value) is not None


def _is_traceparent(value: str) -> bool:
    return _TRACEPARENT.fullmatch(value) is not None


@final
@dataclass(frozen=True, slots=True)
class Envelope:
    """One event: who emitted it, about whom, when, and the serialized payload.

    ``type`` is a protobuf full name (ASCII, at least one dot); ``source`` is visible ASCII without spaces;
    both are at most ``MAX_ATTRIBUTE_LENGTH`` long. ``time`` is an aware ``datetime``; ``traceparent`` is a
    valid W3C trace context or ``None``. Anything else is a ``MalformedEventError``. A message declared
    without a package has no such full name and cannot be wrapped.
    """

    id: UUID
    type: str
    source: str
    tenant: Tenant | None
    time: datetime
    data: bytes
    traceparent: str | None = None

    def __post_init__(self) -> None:
        if not _is_attribute(_FULL_NAME, self.type):
            raise MalformedEventError(_TYPE_HEADER, self.type)
        if not _is_attribute(_TOKEN, self.source):
            raise MalformedEventError(_SOURCE_HEADER, self.source)
        if self.time.utcoffset() is None:
            raise MalformedEventError(_TIME_HEADER, self.time.isoformat())
        if self.traceparent is not None and not _is_traceparent(self.traceparent):
            raise MalformedEventError(_TRACEPARENT_HEADER, self.traceparent)

    @classmethod
    def wrap(
        cls, message: Message, *, source: str, tenant: Tenant | None, clock: Clock = utc_now
    ) -> Envelope:
        """A new event carrying ``message``."""
        return cls(
            id=new_id(),
            type=message.DESCRIPTOR.full_name,
            source=source,
            tenant=tenant,
            time=clock(),
            data=message.SerializeToString(deterministic=True),
        )

    def unwrap[M: Message](self, kind: builtins.type[M]) -> M:
        """The payload as ``kind``; refuses another type or bytes that do not decode.

        ``builtins.type``: inside this class body, mypy reads a plain ``type`` as the field of that name.
        """
        expected = kind.DESCRIPTOR.full_name
        if self.type != expected:
            raise UnexpectedEventError(expected, self.type)
        try:
            return kind.FromString(self.data)
        except DecodeError as error:
            raise MalformedEventError(_DATA_FIELD) from error

    def headers(self) -> dict[str, str]:
        """The NATS headers that carry this envelope (the payload travels as the message body)."""
        headers = {
            _SPECVERSION_HEADER: SPEC_VERSION,
            _ID_HEADER: str(self.id),
            _TYPE_HEADER: self.type,
            _SOURCE_HEADER: self.source,
            _TIME_HEADER: self.time.isoformat(),
            _CONTENT_TYPE_HEADER: CONTENT_TYPE,
            _MESSAGE_ID_HEADER: str(self.id),
        }
        if self.tenant is not None:
            headers[_ORGANIZATION_HEADER] = str(self.tenant.organization)
            headers[_COMPANY_HEADER] = str(self.tenant.company)
        if self.traceparent is not None:
            headers[_TRACEPARENT_HEADER] = self.traceparent
        return headers

    @classmethod
    def from_headers(cls, headers: Mapping[str, str], data: bytes) -> Envelope:
        """Read an envelope back; a missing or invalid attribute is a ``MalformedEventError``.

        An unusable ``traceparent`` is only dropped: losing a trace link must not lose the event.
        """
        _require_exactly(headers, _SPECVERSION_HEADER, SPEC_VERSION)
        _require_exactly(headers, _CONTENT_TYPE_HEADER, CONTENT_TYPE)
        traceparent = headers.get(_TRACEPARENT_HEADER)
        return cls(
            id=_id(headers, _ID_HEADER),
            type=headers.get(_TYPE_HEADER, ""),
            source=headers.get(_SOURCE_HEADER, ""),
            tenant=_tenant(headers),
            time=_time(headers),
            data=data,
            traceparent=traceparent if traceparent is not None and _is_traceparent(traceparent) else None,
        )
