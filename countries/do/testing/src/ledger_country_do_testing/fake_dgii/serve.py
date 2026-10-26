"""``ledger-fake-dgii``: the fake DGII served by granian on 127.0.0.1, port 8471 unless told otherwise.

It listens on the loopback interface only: the fake trusts any signer and hands out tokens to anyone, so it is
never reachable from another machine. In its container (the local stack's ``fake-dgii``, dev only, whose
image sets ``LEDGER_FAKE_DGII_CONTAINER=1``) ``--host`` may name the container's own interfaces, for the
stack's apps to reach it by its service name; compose publishes it on the host's loopback alone. Port 8000
is never taken (another project of the owner's uses it). Stop it with Ctrl+C, or by the PID it was started
with; never by image name.
"""

import argparse
import ipaddress
import os
from typing import Final, final
lazy from collections.abc import Sequence

from granian import Granian
from granian.constants import Interfaces

DEFAULT_HOST: Final = "127.0.0.1"
CONTAINER: Final = "LEDGER_FAKE_DGII_CONTAINER"
"""Set to ``1`` by the fake's image alone: there ``--host`` may name an address beyond loopback."""
DEFAULT_PORT: Final = 8471
RESERVED_PORT: Final = 8000
_HIGHEST_PORT: Final = 65535
_TARGET: Final = "ledger_country_do_testing.fake_dgii.app:create_app"


@final
class _Options(argparse.Namespace):
    port: int = DEFAULT_PORT
    host: str = DEFAULT_HOST


def main(argv: Sequence[str] | None = None) -> None:
    """Serve the fake DGII on the loopback interface until interrupted."""
    parser = argparse.ArgumentParser(
        prog="ledger-fake-dgii",
        description=f"A fake of DGII's e-CF web services, on {DEFAULT_HOST} only.",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="port to listen on (default: %(default)s)"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="address to listen on: loopback, except in its container"
    )
    options = parser.parse_args(argv, namespace=_Options())
    if not _allowed(options.host):
        parser.error("the fake listens on loopback only, outside its container")
    if not 0 < options.port <= _HIGHEST_PORT:
        parser.error(f"a port is between 1 and {_HIGHEST_PORT}")
    if options.port == RESERVED_PORT:
        parser.error(f"port {RESERVED_PORT} is reserved on this machine")
    Granian(
        target=_TARGET, factory=True, address=options.host, port=options.port, interface=Interfaces.ASGI
    ).serve()


def _allowed(host: str) -> bool:
    """Whether the fake may listen on ``host``: an address, loopback unless it runs in its container."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or os.environ.get(CONTAINER) == "1"
