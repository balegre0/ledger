"""Channels as fiscal routes to them (spec §8.3, §8.4 ``channels``): the Protocol, its definition, answers.

A channel is one authority format of one regime (``do-ecf-v1``). Its pack assembles it
(``ChannelParts.channel``) and the app adds it to the platform's ``AssembledChannels``; ``fiscal`` reads it
through a ``ChannelRegistry`` at call time, never while it assembles. Every method takes a ``SystemCall``
whose scope is the tenant the call is about; the channel opens its own units (none may be open when it is
called).

What a channel answers about a document is a ``StatusUpdate``: its own state (a word only it knows), the
platform's ``FiscalStatus`` and ``Severity``, the document's version in the channel (a newer one wins), the
authority's number and messages (verbatim, within the kit's bounds) and details under the regime's key. The
same update is the payload of the kit's ``fiscal_channel.document.updated`` (``events``).
"""

import copy
import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, NewType, Protocol, final, runtime_checkable
from uuid import UUID
lazy from collections.abc import Iterable, Mapping, Sequence
lazy from datetime import date, datetime, timedelta

from ledger_fiscal_kit.status import MAX_MESSAGE_CODE, MAX_MESSAGE_TEXT, MAX_MESSAGES
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.regimes import Direction, DocumentKind
from ledger_kernel.tenancy import Mode
from ledger_platform.runtime.keys import BindingTypeError
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_fiscal_kit.status import AuthorityMessage, FiscalStatus, Severity
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.regimes import FieldDef, Locale, RegimeKey, Source
lazy from ledger_platform.config.manifest import ChannelEntry
lazy from ledger_platform.data.codec import JsonValue
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_platform.runtime.contexts import AssembledChannels

_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")

EnrollmentId = NewType("EnrollmentId", UUID)
"""An enrollment's id (fiscal's): a company in a mode, on a channel, in one of its environments."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelDef:
    """What a channel is: its key and regime, when it is valid, where it submits, what it covers."""

    key: str
    regime: RegimeKey
    valid_from: date
    valid_until: date | None
    environments: tuple[str, ...]
    """The authority's environments it may submit to (``testecf``, ``certecf``, ``ecf``)."""
    live_environments: tuple[str, ...]
    """Those of ``environments`` that serve live documents (``mode_of``): the others serve test ones only."""
    document_kinds: tuple[DocumentKind, ...]
    directions: tuple[Direction, ...]
    fields: tuple[FieldDef, ...]
    """The format-level fields it reads under ``fiscal.<regime>`` beyond the regime's own."""
    labels: Mapping[Locale, str] = field(hash=False)
    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        if not set(self.live_environments) <= set(self.environments):
            raise InvalidValueError(
                field="channel_def.live_environments", requirement="some of the channel's environments"
            )
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise InvalidValueError(field="channel_def.valid_until", requirement="no earlier than valid_from")
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))

    @classmethod
    def of(
        cls,
        entry: ChannelEntry,
        *,
        regime: RegimeKey,
        fields: Iterable[FieldDef],
        labels: Mapping[Locale, str],
        sources: Iterable[Source],
    ) -> ChannelDef:
        """The definition of the channel ``entry`` declares, with what its pack adds to its manifest."""
        return cls(
            key=entry.key,
            regime=regime,
            valid_from=entry.valid_from,
            valid_until=entry.valid_until,
            environments=entry.environments,
            live_environments=entry.live_environments,
            document_kinds=tuple(DocumentKind(kind) for kind in entry.document_kinds),
            directions=tuple(Direction(direction) for direction in entry.directions),
            fields=tuple(fields),
            labels=labels,
            sources=tuple(sources),
        )

    def covers(self, kind: DocumentKind, direction: Direction) -> bool:
        """Whether the channel issues documents of ``kind`` in ``direction``."""
        return kind in self.document_kinds and direction in self.directions

    def mode_of(self, environment: str) -> Mode:
        """The mode ``environment`` serves: live for a live environment, test for the authority's others.

        ``InvalidValueError`` for an environment the channel does not have: a caller that reads one from
        outside (an enrollment request, the receiver's path) checks ``environments`` first.
        """
        if environment not in self.environments:
            raise InvalidValueError(field="channel_def.environment", requirement="one of the channel's")
        return Mode.LIVE if environment in self.live_environments else Mode.TEST

    def allows(self, environment: str, mode: Mode) -> bool:
        """Whether a document of ``mode`` may go to ``environment``: one of the channel's, of that mode.

        A live document goes to a live environment only, and a test document never does.
        """
        return environment in self.environments and self.mode_of(environment) is mode


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EnrollmentState:
    """An enrollment as a channel judges its requirements: whose, its mode and environment, whether active."""

    enrollment: EnrollmentId
    organization: OrganizationId
    company: CompanyId
    mode: Mode
    environment: str
    active: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Requirements:
    """What an enrollment still needs before the channel can issue for it (``do_ecf.range``...)."""

    currently_due: tuple[str, ...]
    disabled_reason: str | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Objection:
    """Why a channel would refuse a draft: a rule's code, the field it is about, a message naming the rule.

    The message names the rule, never the value: it is shown to the client and may be logged.
    """

    code: str
    field: str | None
    message: str

    def __post_init__(self) -> None:
        if _CODE.fullmatch(self.code) is None:
            raise InvalidValueError(field="objection.code", requirement="a snake_case code")


def _kept(message: AuthorityMessage) -> bool:
    return len(message.code) <= MAX_MESSAGE_CODE and len(message.message) <= MAX_MESSAGE_TEXT


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class StatusUpdate:
    """A channel document's state at ``version`` (see the module)."""

    document: DocumentRef
    channel: str
    environment: str
    state: str
    """The channel's own state (``signed``, ``accepted``...): fiscal shows it, never interprets it."""
    status: FiscalStatus
    severity: Severity
    version: int
    """From 1, one more at each change: an update older than the one applied is ignored."""
    authority_number: str | None
    """The number the document carries for the authority (an e-NCF), once it has one."""
    messages: tuple[AuthorityMessage, ...]
    """At most ``MAX_MESSAGES``, each within the kit's bounds (``status.kept``)."""
    details: Mapping[str, JsonValue] = field(hash=False)
    """Under the regime's key (``{"do": {"encf": ..., "security_code": ...}}``): a deep copy, read-only at
    its top level."""
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidValueError(field="status_update.version", requirement="1 or more")
        if len(self.messages) > MAX_MESSAGES or not all(_kept(message) for message in self.messages):
            raise InvalidValueError(
                field="status_update.messages", requirement="kept: at most 20, each within its bounds"
            )
        if self.occurred_at.utcoffset() is None:
            raise InvalidValueError(field="status_update.occurred_at", requirement="an aware datetime")
        object.__setattr__(self, "details", MappingProxyType(copy.deepcopy(dict(self.details))))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Issued:
    """What ``Channel.issue`` answers: the document's state, and whether it was signed within the budget."""

    status: StatusUpdate
    signed: bool


class ResumeReason(StrEnum):
    """Why documents a channel parked may move again."""

    CERTIFICATE_INSTALLED = "certificate_installed"
    RANGE_REGISTERED = "range_registered"
    OPERATOR = "operator"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Representation:
    """A document's printed representation: its media type, its bytes (never shown) and a file name."""

    media_type: str
    content: bytes = field(repr=False)
    filename: str


@runtime_checkable
class Channel(Protocol, metaclass=ABCMeta):
    """One authority format of one regime, as fiscal routes to it (spec §8.3).

    The members are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @property
    @abstractmethod
    def definition(self) -> ChannelDef:
        """What the channel is."""

    @abstractmethod
    async def requirements(self, call: SystemCall, state: EnrollmentState) -> Requirements:
        """What ``state``'s enrollment still needs (a certificate, a numbering range), read now."""

    @abstractmethod
    async def check(self, call: SystemCall, draft: FiscalDraft) -> Sequence[Objection]:
        """Why the channel would refuse ``draft``; empty when it would issue it. Writes nothing."""

    @abstractmethod
    async def issue(self, call: SystemCall, draft: FiscalDraft, *, sign_within: timedelta | None) -> Issued:
        """Number and store ``draft``'s document once per (document, channel): a second call answers it.

        Signs it before answering when ``sign_within`` is given and the signer answers in time; otherwise the
        channel's job signs it.
        """

    @abstractmethod
    async def status(self, call: SystemCall, document: DocumentRef) -> StatusUpdate | None:
        """``document``'s state in the channel; ``None`` if the channel never issued it."""

    @abstractmethod
    async def resume(self, call: SystemCall, company: CompanyId, reason: ResumeReason) -> None:
        """Move the documents of ``company`` (in the call's mode) that ``reason`` may unblock."""

    @abstractmethod
    async def representation(self, call: SystemCall, document: DocumentRef) -> Representation:
        """``document``'s printed representation."""


@final
class ChannelRegistry:
    """The assembled channels, each as a ``Channel``: read at call time only."""

    __slots__ = ("_channels",)

    def __init__(self, channels: AssembledChannels) -> None:
        self._channels: Final = channels

    def get(self, key: str) -> Channel:
        """The channel of ``key``.

        ``ChannelUnknownError`` if none is assembled, ``BindingTypeError`` if it is no ``Channel``.
        """
        channel = self._channels.get(key)
        if not isinstance(channel, Channel):
            raise BindingTypeError(key)
        return channel

    def all(self) -> tuple[Channel, ...]:
        """Every assembled channel, by key."""
        assembled = self._channels.keys()
        return tuple(self.get(key) for key in assembled)
