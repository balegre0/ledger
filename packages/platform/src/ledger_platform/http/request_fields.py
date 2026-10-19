"""What a request learned of its caller, kept where the chassis's report of its failure finds it.

A route's dependencies learn who calls and for which company (``organization_id``, ``subject``,
``company_id``), and bind it to the log context for as long as the request runs through them. A failure is
reported outside them, by the chassis's error handling, once they have ended: so they also leave the fields in
the request's ASGI scope (the mapping Starlette's ``request.state`` lives in too), which that handling reads.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, final
lazy from collections.abc import Generator, Mapping, MutableMapping

import structlog
lazy from fastapi import Request

_KEY: Final = "ledger.caller"
"""Where in the scope of a request the fields are kept."""
_NONE: Final[Mapping[str, str]] = MappingProxyType({})


@final
@dataclass(frozen=True, slots=True)
class _Fields:
    """The fields themselves: a type of its own, so that nothing else under the key is taken for them."""

    values: Mapping[str, str]


def remember(scope: MutableMapping[str, object], **fields: str) -> None:
    """Keep ``fields`` in ``scope`` beside those kept before (a field given again is replaced)."""
    scope[_KEY] = _Fields(MappingProxyType({**remembered(scope), **fields}))


def remembered(scope: Mapping[str, object]) -> Mapping[str, str]:
    """The fields kept in ``scope``; none when nothing learned who calls."""
    match scope.get(_KEY):
        case _Fields(values=values):
            return values
        case _:
            return _NONE


@contextmanager
def bound(request: Request, **fields: str) -> Generator[None]:
    """``fields`` in the log context while the block runs, and in the request for its failure's log line."""
    remember(request.scope, **fields)
    with structlog.contextvars.bound_contextvars(**fields):
        yield
