"""Ports a test holds so that nothing else can bind them: for the tests of what a taken port does."""

import socket
from typing import Final

from pydantic import TypeAdapter

_ADDRESS: Final = TypeAdapter(tuple[str, int] | tuple[str, int, int, int])
"""An internet address as a socket reports it: host and port, plus flow and scope for IPv6."""


def occupy_a_port() -> socket.socket:
    """A listening socket on every interface, the way grpc's ``[::]`` listens: nobody else binds its port."""
    if socket.has_dualstack_ipv6():
        return socket.create_server(("::", 0), family=socket.AF_INET6, dualstack_ipv6=True)
    return socket.create_server(("", 0))


def port_of(listener: socket.socket) -> int:
    """The port a socket listens on; ``getsockname`` answers ``Any``, its shape is the family's."""
    return _ADDRESS.validate_python(listener.getsockname())[1]
