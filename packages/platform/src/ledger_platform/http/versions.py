"""``Ledger-Version`` (§9.2, ADR 0008): the dated versions of the API, and how a request names one.

``/v1`` is the major version; inside it the API has dated versions, named ``YYYY-MM-DD``. A request says which
it speaks in ``Ledger-Version``; without one it speaks its API client's pin, else its organization's, else the
current version (``standing.resolve``). Every answer names the version it was given (``Ledger-Version``),
even a failure's.

Built now: parsing, the precedence and the echo; what a version changes (the transforms of requests and
responses between versions) is built with the first breaking change (§16.4), so for now every known version
is served alike. A date that is no known version, in whatever form, is refused ``version_unknown`` (400) and
never repeated.
"""

from datetime import date
from typing import ClassVar, Final, NewType

from starlette.datastructures import Headers
lazy from starlette.types import Scope

from ledger_platform.http.documents import is_array, operations_of
from ledger_platform.http.problems import BadRequestError

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


def sent_version(scope: Scope) -> ApiVersion | None:
    """The version a request names, if exactly one that exists; never refuses (the caller does)."""
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
