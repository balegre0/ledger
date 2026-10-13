"""What every HTTP exchange of the channel shares: its time limits and how an answer's body is read.

DGII's client and the buyer client both wait by ``DgiiTimeouts`` and read answers with ``bounded``: plain or
a single gzip layer (``Accept-Encoding: gzip``), decompressed as it streams and refused once it passes
``MAX_ANSWER`` (1 MiB), never read whole first; any other encoding is refused.
"""

import zlib
from dataclasses import dataclass
from typing import Final, final

lazy import httpx

MAX_ANSWER: Final = 1024 * 1024
ACCEPTED_ENCODING: Final = "gzip"
_PLAIN: Final = "identity"
_GZIP_WINDOW: Final = 31
"""``zlib``'s ``wbits`` for exactly one gzip member (16 + a 32 KiB window)."""


@final
@dataclass(frozen=True, slots=True)
class DgiiTimeouts:
    """How long a party is waited for, in seconds: to connect and in all per attempt; per call, overall."""

    connect: float = 5.0
    total: float = 20.0
    call: float = 60.0
    """A call's deadline: its token, every attempt, the waits between them."""


DEFAULT_TIMEOUTS: Final = DgiiTimeouts()


async def bounded(response: httpx.Response) -> bytes | None:
    """The body, plain or one gzip layer decompressed as it streams; ``None`` if unreadable or too large."""
    encodings = [
        each.strip().lower() for each in response.headers.get_list("content-encoding", split_commas=True)
    ]
    if encodings not in ([], [_PLAIN], [ACCEPTED_ENCODING]):
        return None
    inflater = zlib.decompressobj(wbits=_GZIP_WINDOW) if encodings == [ACCEPTED_ENCODING] else None
    body = bytearray()
    try:
        async for chunk in response.aiter_raw():
            body += chunk if inflater is None else inflater.decompress(chunk, MAX_ANSWER + 1 - len(body))
            if len(body) > MAX_ANSWER:
                return None
    except zlib.error:
        return None
    if inflater is not None and (not inflater.eof or inflater.unused_data):
        return None
    return bytes(body)
