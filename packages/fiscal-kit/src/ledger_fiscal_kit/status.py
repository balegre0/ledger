"""What a tax authority's answer means, country-neutral (spec §8.3, §8.4 ``status``).

A channel maps each code its authority documents for a service to one of its own states, a ``FiscalStatus``
(the platform's word for it), a ``Severity`` (what it asks of the platform) and whether the authority has
decided. ``StatusMap`` holds that mapping and checks it when it is built (so at import time): every
documented code has an entry and no entry maps a code never documented. A code the authority answers that the
map does not hold is never guessed at: ``UnknownAuthorityCodeError``, read as an outage.

The authority's messages are kept verbatim, never translated (spec §7.10), and bounded: at most
``MAX_MESSAGES``, each text at most ``MAX_MESSAGE_TEXT`` characters (spec §8.4, kit events).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, final
lazy from collections.abc import Collection, Mapping, Sequence

from ledger_kernel.errors import LedgerError, UnavailableError, excerpt, truncate

MAX_MESSAGES: Final = 20
MAX_MESSAGE_TEXT: Final = 500
MAX_MESSAGE_CODE: Final = 64
_MARK: Final = len("...")
"""What ``truncate`` appends to a text it cuts: a kept text is cut shorter by as much, to stay in bounds."""


class FiscalStatus(StrEnum):
    """The country-neutral status of a fiscal document."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_OBSERVATIONS = "accepted_with_observations"
    REJECTED = "rejected"
    VOIDED = "voided"


class Severity(StrEnum):
    """What an outcome asks of the platform: nothing, attention, or a person (spec §8.3)."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@final
@dataclass(frozen=True, slots=True)
class StatusEntry[S: StrEnum]:
    """Where an authority's code leads: the channel's state ``S``, the fiscal status, the severity."""

    state: S
    status: FiscalStatus
    severity: Severity
    final: bool
    """Whether the authority has decided."""


def _shown(code: int | str) -> str:
    """``code`` for a message: a number as it is, a text quoted and cut (it came from outside)."""
    return excerpt(code) if isinstance(code, str) else str(code)


class UnmappedAuthorityCodeError(LedgerError):
    """A status map that is not exhaustive: a defect of the channel, found when the map is built."""

    code: ClassVar[str] = "authority_code_unmapped"

    def __init__(self, found: int | str, *, documented: bool) -> None:
        problem = "has no entry" if documented else "has an entry but is not documented"
        super().__init__(f"the status map is not exhaustive: code {_shown(found)} {problem}")


class UnknownAuthorityCodeError(UnavailableError):
    """The authority answered a code it never documented for the service: an outage, retried later."""

    code: ClassVar[str] = "authority_code_unknown"

    def __init__(self, found: int | str) -> None:
        super().__init__(
            f"the authority answered code {_shown(found)}, which it never documented for the service"
        )


@final
class StatusMap[C: int | str, S: StrEnum]:
    """Each code the authority documents for a service, mapped (see the module)."""

    __slots__ = ("_entries",)

    def __init__(self, entries: Mapping[C, StatusEntry[S]], *, documented: Collection[C]) -> None:
        for code in documented:
            if code not in entries:
                raise UnmappedAuthorityCodeError(code, documented=True)
        for code in entries:
            if code not in documented:
                raise UnmappedAuthorityCodeError(code, documented=False)
        self._entries: Final[dict[int | str, StatusEntry[S]]] = dict(entries.items())

    def entry(self, code: int | str) -> StatusEntry[S]:
        """Where ``code`` leads; ``UnknownAuthorityCodeError`` if the map does not hold it."""
        found = self._entries.get(code)
        if found is None:
            raise UnknownAuthorityCodeError(code)
        return found


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorityMessage:
    """One of the authority's messages, verbatim: never translated, never logged (spec §7.10)."""

    code: str
    """A number or a word, as text."""
    message: str
    lang: str
    """The language the authority wrote it in (``es``)."""


def _bounded(text: str, limit: int) -> str:
    """``text`` if it fits in ``limit`` characters; cut, its mark included, to ``limit`` otherwise."""
    return text if len(text) <= limit else truncate(text, limit - _MARK)


def kept(messages: Sequence[AuthorityMessage]) -> tuple[AuthorityMessage, ...]:
    """The first ``MAX_MESSAGES`` of ``messages``, each code and text cut to its bound."""
    return tuple(
        AuthorityMessage(
            code=_bounded(message.code, MAX_MESSAGE_CODE),
            message=_bounded(message.message, MAX_MESSAGE_TEXT),
            lang=message.lang,
        )
        for message in messages[:MAX_MESSAGES]
    )
