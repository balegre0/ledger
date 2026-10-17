"""``Ledger-Version`` (§9.2, ADR 0008): the dated version of the API a request speaks, and what pins it.

``/v1`` is the major version; inside it the API has dated versions, named ``YYYY-MM-DD``. A request says which
it speaks in ``Ledger-Version``; without one it speaks its API client's pin, else its organization's, else the
current version. Every answer names the version it was given (``Ledger-Version``), even a failure's.

Built now: parsing, the precedence and the echo; what a version changes (the transforms of requests and
responses between versions) is built with the first breaking change (§16.4), so for now every known version
is served alike. A date that is no known version, in whatever form, is refused ``version_unknown`` (400) and
never repeated.

The pins are stored by identity (an organization's and an API client's, fixed at creation); the chassis
reads them through ``Pins``.
"""

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import ClassVar, Final, NewType, Protocol, final, override

from starlette.datastructures import Headers
lazy from starlette.types import Scope

from ledger_platform.config.options import require_at_least, require_seconds
from ledger_platform.http.documents import is_array, operations_of
from ledger_platform.http.problems import BadRequestError
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_kernel.ids import OrganizationId

VERSION_HEADER: Final = "Ledger-Version"
VERSION_KEY: Final = "ledger.version"
"""Where the request's version waits in its ASGI scope for the answer to name."""
ApiVersion = NewType("ApiVersion", date)
"""One dated version of the API."""
KNOWN_VERSIONS: Final = (ApiVersion(date(2026, 10, 13)),)
"""Every version the API serves, oldest first. A new version is added at the end, with its change."""
CURRENT_VERSION: Final = KNOWN_VERSIONS[-1]
"""The version of a request that names none and has no pin; what an organization or API client created now
is pinned to."""
_DESCRIPTION: Final = "The dated version of the API the request speaks (YYYY-MM-DD); its pin otherwise."


class VersionUnknownError(BadRequestError):
    """``Ledger-Version`` names no version the API serves, or is sent more than once."""

    code: ClassVar[str] = "version_unknown"

    def __init__(self) -> None:
        known = ", ".join(version.isoformat() for version in KNOWN_VERSIONS)
        super().__init__(f"{VERSION_HEADER} is sent once, as one of: {known}")


def _is_written_date(text: str) -> bool:
    """Whether ``text`` is ``YYYY-MM-DD`` in ASCII digits and nothing else."""
    return (
        len(text) == len("2026-10-13")
        and text[4] == text[7] == "-"
        and all(character in "0123456789" for character in text[:4] + text[5:7] + text[8:])
    )


def parse_version(text: str) -> ApiVersion:
    """The version ``text`` names; ``VersionUnknownError`` for anything but a known date written as above."""
    if _is_written_date(text):
        try:
            found = date.fromisoformat(text)
        except ValueError:
            raise VersionUnknownError from None
        if found in KNOWN_VERSIONS:
            return ApiVersion(found)
    raise VersionUnknownError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Pinned:
    """The versions a caller is pinned to, where it is: its API client's and its organization's."""

    client: ApiVersion | None
    organization: ApiVersion | None


_UNPINNED: Final = Pinned(client=None, organization=None)


def resolve(sent: ApiVersion | None, pinned: Pinned) -> ApiVersion:
    """The version a request speaks: what it sent, else its client's pin, its organization's, the current."""
    for version in (sent, pinned.client, pinned.organization):
        if version is not None:
            return version
    return CURRENT_VERSION


class Pins(Protocol, metaclass=ABCMeta):
    """Where the pins of a caller come from: identity keeps them (an organization's and an API client's).

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def of(self, principal: Principal) -> Pinned:
        """The pins of ``principal``'s API client, if it is one, and of its organization."""


@final
class CachedPins(Pins):
    """``Pins`` remembered for ``ttl`` seconds, for at most ``size`` callers (the oldest are forgotten first).

    A pin is fixed when its organization or API client is made and rarely changes, yet every request without
    ``Ledger-Version`` asks for it: this keeps that question off the database. A pin changed shows within
    ``ttl``. Nothing is remembered of a question that failed, nor of an answer without a pin: it is what a
    caller of an organization that does not exist yet gets, and its next question must see the organization
    once it does.
    """

    __slots__ = ("_clock", "_inner", "_kept", "_size", "_ttl")

    def __init__(self, inner: Pins, *, ttl: float, size: int, clock: MonotonicClock = monotonic) -> None:
        require_seconds("pins", "ttl", ttl)
        require_at_least("pins", "size", size)
        self._inner = inner
        self._ttl = ttl
        self._size = size
        self._clock = clock
        self._kept: OrderedDict[tuple[OrganizationId, SubjectId], tuple[float, Pinned]] = OrderedDict()

    @override
    async def of(self, principal: Principal) -> Pinned:
        key, now = (principal.organization, principal.subject), self._clock()
        held = self._kept.get(key)
        if held is not None and now < held[0]:
            return held[1]
        pinned = await self._inner.of(principal)
        _ = self._kept.pop(key, None)
        if pinned != _UNPINNED:
            self._kept[key] = (now + self._ttl, pinned)
            while len(self._kept) > self._size:
                _ = self._kept.popitem(last=False)
        return pinned


def sent_version(scope: Scope) -> ApiVersion | None:
    """The version a request names, if it names exactly one that exists; never refuses (see ``versioned``)."""
    sent = Headers(scope=scope).getlist(VERSION_HEADER)
    if len(sent) != 1:
        return None
    try:
        return parse_version(sent[0])
    except VersionUnknownError:
        return None


def document_version_header(document: dict[str, object]) -> None:
    """Document the optional ``Ledger-Version`` request header on each operation of an OpenAPI ``document``.

    Every route answers with the version it speaks, and one that proves its caller reads the header. In place.
    """
    header: dict[str, object] = {
        "name": VERSION_HEADER,
        "in": "header",
        "required": False,
        "description": _DESCRIPTION,
        "schema": {
            "type": "string",
            "format": "date",
            "enum": [version.isoformat() for version in KNOWN_VERSIONS],
        },
    }
    for operation in operations_of(document):
        parameters = operation.get("parameters")
        operation["parameters"] = [*(parameters if is_array(parameters) else []), header]
