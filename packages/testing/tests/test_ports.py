import errno
import socket

import pytest

from ledger_testing.ports import occupy_a_port, port_of


def in_use(refused: OSError) -> bool:
    return refused.errno == errno.EADDRINUSE


def bind_again(taken: socket.socket) -> None:
    """Listen on ``taken``'s port as it listens (grpc's ``[::]``: both stacks, or every IPv4 interface)."""
    port = port_of(taken)
    if taken.family == socket.AF_INET6:
        socket.create_server(("::", port), family=socket.AF_INET6, dualstack_ipv6=True).close()
    else:
        socket.create_server(("", port)).close()


def test_an_occupied_port_cannot_be_bound_again() -> None:
    with occupy_a_port() as taken:
        assert port_of(taken) > 0
        with pytest.raises(OSError, check=in_use):
            bind_again(taken)


def test_without_a_dual_stack_the_port_is_taken_on_every_ipv4_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "has_dualstack_ipv6", lambda: False)
    with occupy_a_port() as taken:
        assert taken.family == socket.AF_INET
        with pytest.raises(OSError, check=in_use):
            bind_again(taken)
