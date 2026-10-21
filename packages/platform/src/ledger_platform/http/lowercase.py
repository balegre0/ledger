"""Case-insensitive paths under some prefixes (§3.8): the tax authority calls the receiver in any case.

DGII's services are case-insensitive ([06] §4), and the URLs a taxpayer registers with it are called as DGII
spells them (``/fe/Recepcion/api/ecf``, ``/FE/RECEPCION/API/ECF``). ``LowercasePath`` lower-cases the ASCII
letters of a request's ``path`` before the app routes it, when the lower-cased path starts with one of its
prefixes (the receiver's channel keys); any other path is routed as sent. Only ASCII letters change: a
character outside ASCII whose lower case is an ASCII letter (the Kelvin sign's is ``k``) stays itself and
reaches no route. ``raw_path`` is kept as sent.

The scope is changed in place: the layers around it read what the router leaves in it (the route a request
took, which its metrics are labelled by), and the body limit matches its routes on the lower-cased path.
"""

import re
import string
from typing import Final, final
lazy from collections.abc import Sequence

from ledger_platform.config.options import InvalidOptionError
lazy from ledger_platform.http.metrics import AsgiApp, AsgiReceive, AsgiScope, AsgiSend

_ASCII_LOWER: Final = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_PREFIX: Final = re.compile(r"/(?:[a-z0-9._~-]+/)+", re.ASCII)
"""Whole lower-case ASCII segments, from the root, ending with ``/``: a prefix never cuts a segment."""


def ascii_lower(text: str) -> str:
    """``text`` with its ASCII capitals lower-cased, and nothing else changed."""
    return text.translate(_ASCII_LOWER)


@final
class LowercasePath:
    """Routes the paths under ``prefixes`` lower-cased (see the module); an ASGI middleware."""

    __slots__ = ("_app", "_prefixes")

    def __init__(self, app: AsgiApp, *, prefixes: Sequence[str]) -> None:
        for prefix in prefixes:
            if _PREFIX.fullmatch(prefix) is None:
                raise InvalidOptionError(
                    component="http",
                    option="lowercase_prefixes",
                    requirement="whole lower-case ASCII segments from the root, ending with /",
                    value=prefix,
                )
        self._app = app
        self._prefixes = tuple(prefixes)

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Route the request lower-cased when its path is under a prefix, else as sent."""
        path = scope.get("path")
        if (
            scope["type"] == "http"
            and isinstance(path, str)
            and (lowered := ascii_lower(path)).startswith(self._prefixes)
        ):
            scope["path"] = lowered
        await self._app(scope, receive, send)
