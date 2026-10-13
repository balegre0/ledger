"""A buyer's endpoints, as DGII's directory gives them: the buyer writes them, hostile until checked.

A buyer registers its URLs with DGII, and DGII's directory hands them to every issuer: whoever controls a
buyer's entry chooses where an issuer's requests go. Before anything is sent:

- **The URL.** ``https``, a host name (letters, digits, hyphens; at least two labels; no IP literal in any
  spelling, no user information), an optional port, a path; no query or fragment (``endpoint``).
- **The address.** The name is resolved and **every** address it resolves to must be public (``public``):
  globally routable, not loopback, private, link-local, shared, reserved, documentation, multicast or
  unspecified, nor an IPv6 form that carries or translates to such an IPv4 address (mapped, 6to4, Teredo,
  NAT64). The request then goes to the address checked (``pinned``), never to the name again: a second
  resolution, rebound to an internal address between the check and the connection, is never made. The
  name still decides TLS (``sni_hostname``: the certificate is verified for the name) and ``Host``.

Each request resolves and checks afresh: a rebinding between two requests is caught by the second check.
Redirects are never followed, answers are bounded and time-limited (``client``). A refusal never quotes the
URL.
"""

import asyncio
import re
import socket
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, IPv6Network, ip_address
from typing import ClassVar, Final, final
from urllib.parse import urlsplit
lazy from collections.abc import Awaitable, Callable, Sequence

from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError

type Address = IPv4Address | IPv6Address
type Resolve = Callable[[str, int], Awaitable[Sequence[Address]]]

HTTPS_PORT: Final = 443
_NAME: Final = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
"""A host name of two labels or more, its last one not all digits (``2130706433`` and ``0x7f000001``-like
spellings of an address never pass: their last label is a number, or they are a single label)."""
_PATH: Final = re.compile(r"(?:/[A-Za-z0-9._~!$&'()*+,;=:@%-]*)*")
_NAT64: Final = (IPv6Network("64:ff9b::/96"), IPv6Network("64:ff9b:1::/48"))
_SCHEME: Final = "it is not https"
_USER: Final = "it carries user information"
_HOST: Final = "its host is not a public host name"
_PORT: Final = "its port is not a port"
_PATH_REASON: Final = "its path is not a plain path, or it carries a query or a fragment"
_ADDRESS: Final = "its host resolves to an address that is not public, or to none"


class BuyerEndpointRefusedError(EcfInvalidError):
    """A buyer's endpoint that may not be reached (see the module); the reason never quotes it."""

    code: ClassVar[str] = "do_ecf_buyer_endpoint_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(f"a buyer's endpoint from DGII's directory is refused: {reason}")


@final
@dataclass(frozen=True, slots=True)
class Endpoint:
    """A checked endpoint: its host name (lower case), port and path (no trailing slash)."""

    host: str
    port: int
    path: str

    @property
    def authority(self) -> str:
        """``Host`` for it: the name, and the port unless it is https's own."""
        return self.host if self.port == HTTPS_PORT else f"{self.host}:{self.port}"

    def url(self, address: Address, suffix: str) -> str:
        """The URL of ``suffix`` under this endpoint, at ``address`` (the one checked), not at the name."""
        literal = f"[{address}]" if isinstance(address, IPv6Address) else str(address)
        return f"https://{literal}:{self.port}{self.path}{suffix}"


def endpoint(url: str) -> Endpoint:
    """``url`` checked (see the module); ``BuyerEndpointRefusedError`` otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise BuyerEndpointRefusedError(_PORT) from error
    if parts.scheme != "https":
        raise BuyerEndpointRefusedError(_SCHEME)
    if "@" in parts.netloc:
        raise BuyerEndpointRefusedError(_USER)
    host = parts.hostname or ""
    if not _NAME.fullmatch(host) or parts.netloc.lower() not in {host, f"{host}:{port}"}:
        raise BuyerEndpointRefusedError(_HOST)
    if port == 0:
        raise BuyerEndpointRefusedError(_PORT)
    if parts.query or parts.fragment or url.endswith(("?", "#")) or not _PATH.fullmatch(parts.path):
        raise BuyerEndpointRefusedError(_PATH_REASON)
    return Endpoint(host, HTTPS_PORT if port is None else port, parts.path.rstrip("/"))


def public(address: Address) -> bool:
    """Whether ``address`` may be reached (see the module)."""
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return public(address.ipv4_mapped)
        if any(address in network for network in _NAT64):
            return False
    return address.is_global and not address.is_multicast


async def system_resolve(host: str, port: int) -> Sequence[Address]:
    """Every address the system's resolver gives ``host`` for a TCP connection to ``port``."""
    found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(ip_address(str(entry[4][0]).partition("%")[0]) for entry in found))


async def pinned(at: Endpoint, resolve: Resolve) -> Address:
    """The address to send to: the first ``at``'s host resolves to, once every one is public."""
    addresses = tuple(await resolve(at.host, at.port))
    if not addresses or not all(public(address) for address in addresses):
        raise BuyerEndpointRefusedError(_ADDRESS)
    return addresses[0]
