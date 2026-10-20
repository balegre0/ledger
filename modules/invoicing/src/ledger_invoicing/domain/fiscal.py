"""The fiscal mirror (§7.5): what each fiscal channel made of a document, as the channel last told it.

A commercial document has its own state machine (``draft → issued → voided``); each channel it was routed to
has another, which only the channel knows. invoicing keeps, per document and channel, the last status the
channel reported (through ``InvoicingApi.apply_fiscal_status``, called by ``fiscal``): the platform's word for
it, the channel's version of it, the authority's number and messages, and the channel's own details under the
regime's key. A newer version replaces an older one; an older or equal one changes nothing
(``Applied.STALE``), so updates may arrive in any order and more than once.

The mirror is not the document's state: applying a status leaves the document's version (its ``ETag``) as it
is. It decides one rule of the document: one a channel holds (any status but rejected or voided: numbered,
submitted, accepted) is never voided; a credit note corrects it. A status for a document voided already is
not kept (``Applied.DOCUMENT_VOIDED``): fiscal applies ``pending`` before it numbers one, so a voided
document is never numbered.

Everything here comes from our own code (a channel, through ``fiscal``), already bounded by the kit: a value
out of these bounds is a defect of its caller (``InvalidValueError``), never a client's input. The
authority's messages are kept verbatim (§7.10) and never logged; a status's ``repr`` shows its channel,
status and version only.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, final, override
lazy from collections.abc import Iterable, Mapping
lazy from datetime import datetime

from ledger_kernel.errors import InvalidValueError

type Detail = str | int | bool | list[Detail] | dict[str, Detail] | None
"""A value of a channel's details: JSON as the platform's codec reads it (the domain, pure, names its own)."""

MAX_ENVIRONMENT: Final = 32
MAX_AUTHORITY_NUMBER: Final = 64
MAX_MESSAGES: Final = 20
MAX_MESSAGE_CODE: Final = 64
MAX_MESSAGE_TEXT: Final = 500
MAX_LANGUAGE: Final = 35
MAX_DETAIL_DEPTH: Final = 5
"""How deep a channel's details nest below their top level (an object of lists of objects...)."""
MAX_DETAILS: Final = 16384
"""The longest a channel's details may be, as JSON."""
"""A BCP 47 language tag, as long as the RFC's longest recommended one."""
_CHANNEL: Final = re.compile(r"[a-z]{2}-[a-z0-9]+-v[0-9]+", re.ASCII)
"""A channel's key, as manifests name it (``do-ecf-v1``)."""
_REGIME: Final = re.compile(r"[a-z]{2}(?:-[a-z0-9]{1,3})?", re.ASCII)
"""A regime's key: its country (``do``), or its subdivision (``us-ca``)."""
_WORD: Final = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)


class FiscalStatusName(StrEnum):
    """Where a channel's document stands, in the platform's words (the kit's ``FiscalStatus``)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_OBSERVATIONS = "accepted_with_observations"
    REJECTED = "rejected"
    VOIDED = "voided"


RELEASING: Final = frozenset({FiscalStatusName.REJECTED, FiscalStatusName.VOIDED})
"""The statuses that leave the document to invoicing: any other means a channel holds it (the module)."""


class Applied(StrEnum):
    """What applying a channel's status did."""

    APPLIED = "applied"
    """It was newer than the one kept (or the first): it is kept now, and announced."""
    STALE = "stale"
    """It was not newer than the one kept for the channel: nothing changed."""
    DOCUMENT_VOIDED = "document_voided"
    """The document is voided: nothing is kept, and the channel must not number or submit it."""


def _bounded(text: str, limit: int) -> bool:
    return 1 <= len(text) <= limit


def _invalid(*, name: str, requirement: str) -> InvalidValueError:
    return InvalidValueError(field=f"fiscal_status.{name}", requirement=requirement)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMessage:
    """One of the authority's messages, verbatim (never translated, never logged)."""

    code: str
    message: str = field(repr=False)
    lang: str

    def __post_init__(self) -> None:
        if not _bounded(self.code, MAX_MESSAGE_CODE):
            raise _invalid(name="messages.code", requirement=f"1 to {MAX_MESSAGE_CODE} characters")
        if len(self.message) > MAX_MESSAGE_TEXT:
            raise _invalid(name="messages.message", requirement=f"at most {MAX_MESSAGE_TEXT} characters")
        if not _bounded(self.lang, MAX_LANGUAGE):
            raise _invalid(
                name="messages.lang", requirement=f"a language tag of 1 to {MAX_LANGUAGE} characters"
            )


@final
@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class FiscalStatus:
    """A channel's document for a commercial document, at the channel's ``version`` (see the module)."""

    channel: str
    """The channel's key (``do-ecf-v1``): one status per document and channel."""
    environment: str
    """The authority's environment the channel submits to (``testecf``, ``ecf``)."""
    status: FiscalStatusName
    version: int
    """From 1, one more at each change in the channel: a newer one wins."""
    authority_number: str | None
    """The number the document carries for the authority (an e-NCF), once it has one."""
    messages: tuple[AuthorityMessage, ...]
    regime: str
    """The regime's key: the namespace ``details`` go under on the wire (``fiscal[].do``)."""
    details: Mapping[str, Detail] = field(hash=False)
    """The channel's own words (``encf``, ``security_code``, ``qr_url``...): a deep copy, read-only at its top
    level. invoicing shows them and never reads them."""
    updated_at: datetime
    """When the channel's document reached this status."""

    def __post_init__(self) -> None:
        if _CHANNEL.fullmatch(self.channel) is None:
            raise _invalid(name="channel", requirement="a channel's key, <cc>-<name>-v<n>")
        if not _bounded(self.environment, MAX_ENVIRONMENT) or _WORD.fullmatch(self.environment) is None:
            raise _invalid(
                name="environment", requirement=f"a lower-case word of at most {MAX_ENVIRONMENT} characters"
            )
        if self.version < 1:
            raise _invalid(name="version", requirement="1 or more")
        number = self.authority_number
        if number is not None and not _bounded(number, MAX_AUTHORITY_NUMBER):
            raise _invalid(name="authority_number", requirement=f"1 to {MAX_AUTHORITY_NUMBER} characters")
        if len(self.messages) > MAX_MESSAGES:
            raise _invalid(name="messages", requirement=f"at most {MAX_MESSAGES}")
        if _REGIME.fullmatch(self.regime) is None:
            raise _invalid(name="regime", requirement="a regime's key")
        if self.updated_at.utcoffset() is None:
            raise _invalid(name="updated_at", requirement="an aware datetime")
        details = dict(self.details)
        deepest = max((_depth(value) for value in details.values()), default=0)
        if deepest > MAX_DETAIL_DEPTH or len(json.dumps(details)) > MAX_DETAILS:
            raise _invalid(
                name="details",
                requirement=f"JSON at most {MAX_DETAIL_DEPTH} levels deep and {MAX_DETAILS} characters long",
            )
        object.__setattr__(self, "details", MappingProxyType(copy.deepcopy(details)))

    @override
    def __repr__(self) -> str:
        shown = f"status={self.status.value!r}, version={self.version}"
        return f"FiscalStatus(channel={self.channel!r}, {shown})"


def held(statuses: Iterable[FiscalStatus]) -> bool:
    """Whether a channel holds the document, as its mirror ``statuses`` say: one is not rejected or voided."""
    return any(status.status not in RELEASING for status in statuses)


def _depth(value: Detail) -> int:
    """How deep ``value`` nests: 0 for a scalar, one more than its deepest member for a list or an object."""
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    if isinstance(value, dict):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    return 0
