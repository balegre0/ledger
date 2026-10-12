"""The error vocabulary of the platform.

Every error raised on purpose is a ``LedgerError``. Its ``kind`` decides how it crosses a boundary (HTTP
status, gRPC status, retry or not); its ``code`` is stable and machine-readable, so API clients can match
on it; its message is for humans and may change. Messages are built inside each error class.
"""

import reprlib
from enum import StrEnum
from typing import ClassVar, Final

_EXCERPT: Final = 40
_MIN_LIMIT: Final = 1
_PEEK: Final = 4
_BOUNDED: Final = reprlib.Repr(
    maxlevel=1,
    maxstring=_EXCERPT,
    maxlong=_EXCERPT,
    maxother=_EXCERPT,
    maxlist=_PEEK,
    maxtuple=_PEEK,
    maxdict=_PEEK,
    maxset=_PEEK,
    maxfrozenset=_PEEK,
    maxdeque=_PEEK,
    maxarray=_PEEK,
)
"""``repr`` with a fixed ceiling: a few items of one level, each cut, whatever the value."""


class ErrorKind(StrEnum):
    """What went wrong, from the caller's point of view."""

    INVALID = "invalid"
    """The request can never succeed as sent."""
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    """The current state forbids it: a duplicate, a wrong state, a stale version."""
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    """A dependency failed; the same request may succeed later."""
    INTERNAL = "internal"


class LedgerError(Exception):
    """Base of every error the platform raises on purpose."""

    kind: ClassVar[ErrorKind] = ErrorKind.INTERNAL
    code: ClassVar[str] = "internal"


class InvalidInputError(LedgerError):
    """The input is malformed or breaks a rule; sending it again cannot work."""

    kind: ClassVar[ErrorKind] = ErrorKind.INVALID
    code: ClassVar[str] = "invalid_input"


class NotFoundError(LedgerError):
    """The thing asked for does not exist (or the caller may not know it exists)."""

    kind: ClassVar[ErrorKind] = ErrorKind.NOT_FOUND
    code: ClassVar[str] = "not_found"


class ConflictError(LedgerError):
    """The current state forbids the operation."""

    kind: ClassVar[ErrorKind] = ErrorKind.CONFLICT
    code: ClassVar[str] = "conflict"


class ForbiddenError(LedgerError):
    """The caller is known but not allowed to do this."""

    kind: ClassVar[ErrorKind] = ErrorKind.FORBIDDEN
    code: ClassVar[str] = "forbidden"


class UnavailableError(LedgerError):
    """A dependency is failing; retrying later may succeed."""

    kind: ClassVar[ErrorKind] = ErrorKind.UNAVAILABLE
    code: ClassVar[str] = "unavailable"


class InvalidTextLimitError(LedgerError):
    """A caller asked ``excerpt`` or ``truncate`` for text cut shorter than one character.

    A bug in our code, never the client's input.

    It stays an ``INTERNAL`` error on purpose. ``InvalidInputError`` means "the request can never succeed
    as sent" and crosses a boundary as a client error; this is a defect to fix and must surface as a server
    error.
    """

    code: ClassVar[str] = "invalid_text_limit"

    def __init__(self, limit: int) -> None:
        super().__init__(f"a text limit must be at least {_MIN_LIMIT}, not {limit}")


def truncate(text: str, limit: int) -> str:
    """``text`` cut at ``limit`` characters, with ``...`` after the cut when anything was dropped.

    For text that comes from elsewhere and must not be shown (or logged) at its full length. The result
    is at most ``limit`` plus the three characters of the mark; a ``limit`` below one is a programming error.
    """
    if limit < _MIN_LIMIT:
        raise InvalidTextLimitError(limit)
    return text if len(text) <= limit else text[:limit] + "..."


def excerpt(text: str, limit: int = _EXCERPT) -> str:
    """``text`` quoted for an error message, cut at ``limit`` characters so input is never echoed whole."""
    return repr(truncate(text, limit))


def described(value: object) -> str:
    """``value`` led by its type, for an error about a value that is not of the type it needed.

    Annotations are not enforced where input crosses a boundary (``json.loads`` hands over whatever was
    sent), so such an error must say what arrived: ``int: 101850043`` is not the text ``101850043``. The type
    comes first so that it survives ``excerpt``. Describing never fails and never grows with the value: a text
    is cut like an excerpt, anything else is shown by ``reprlib`` with a fixed ceiling (a number too big to
    print is described, not raised).
    """
    shown = truncate(value, _EXCERPT) if isinstance(value, str) else _BOUNDED.repr(value)
    return f"{type(value).__name__}: {shown}"
