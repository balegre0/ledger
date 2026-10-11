"""The error vocabulary of the platform.

Every error raised on purpose is a ``LedgerError``. Its ``kind`` decides how it crosses a boundary (HTTP
status, gRPC status, retry or not); its ``code`` is stable and machine-readable, so API clients can match
on it; its message is for humans and may change. Messages are built inside each error class.
"""

from enum import StrEnum
from typing import ClassVar, Final

_EXCERPT: Final = 40


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


def excerpt(text: str, limit: int = _EXCERPT) -> str:
    """``text`` quoted for an error message, cut at ``limit`` characters so input is never echoed whole."""
    if len(text) <= limit:
        return repr(text)
    return repr(text[:limit] + "...")
