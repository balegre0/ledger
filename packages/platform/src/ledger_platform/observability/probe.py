"""``ledger-probe URL``: exit 0 when ``URL`` answers 200 within two seconds, 1 when not, 2 when misused.

A service's runtime image has no shell, curl or wget, only the service's Python: its container health check is
this console script. Plain HTTP to the container itself, standard library only.
"""

import http.client
import sys
from typing import Final
from urllib.parse import urlsplit

TIMEOUT: Final = 2.0
_OK: Final = 200
_UNHEALTHY: Final = 1
_MISUSED: Final = 2
_USAGE: Final = "usage: ledger-probe http://HOST:PORT/PATH\n"


def healthy(url: str, *, timeout: float = TIMEOUT) -> bool:
    """Whether ``url`` (``http://``) answers 200 within ``timeout`` seconds; anything else is not healthy."""
    parts = urlsplit(url)
    try:
        connection = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=timeout)
    except ValueError:  # a port that is not a number
        return False
    try:
        connection.request("GET", parts.path or "/")
        return connection.getresponse().status == _OK
    except OSError, http.client.HTTPException:  # refused, reset, timed out, not HTTP
        return False
    finally:
        connection.close()


def main() -> int:
    """The console script: one ``http://`` URL, the exit code says whether it is healthy."""
    match sys.argv[1:]:
        case [url] if url.startswith("http://"):
            return 0 if healthy(url) else _UNHEALTHY
        case _:
            sys.stderr.write(_USAGE)
            return _MISUSED
