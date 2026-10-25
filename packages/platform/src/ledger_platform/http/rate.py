"""A rate limit per credential, in process (§10): the backstop behind the ingress's own.

GCRA (the generic cell rate algorithm) keeps one number per credential, the time its next request would be due
if it went at the steady rate (its theoretical arrival time). A request is allowed while that time is no more
than a burst's worth ahead of now; one that is not allowed is told how long to wait and uses up nothing, so
asking again earns no further delay. The limiter is per replica: a credential may use the allowance of each
replica it reaches, which is what a backstop for a runaway caller needs.

It remembers a bounded number of credentials (``max_keys``), in the order they were last allowed. Room for a
new one is made from the front, one at a time (constant time per request): the least recently allowed is
forgotten when it is idle (it would have its whole burst back anyway), and set aside with its arrival time
when it is still active, in a second bounded map (``max_set_aside``) it is taken back from when it asks again.
So a new credential never waits for room, and an active one keeps the allowance it used. Only past
``max_keys + max_set_aside`` credentials active at once is the oldest set aside forgotten: the bound a flood
that large meets. The settings are ``LEDGER_RATE_LIMIT_PER_SECOND`` and ``LEDGER_RATE_LIMIT_BURST``.

The receiver's callers hold no credential of ours (§3.8): ``RateByClient`` holds each client *address* to a
limiter instead (§10, the receiver's per-IP limit; ``LEDGER_RECEIVER_RATE_*``), before the app reads anything
of the request. The address is the peer's, unless proxies are declared in front (``forwarded_hops``): then it
is the one the outermost of them appended to ``X-Forwarded-For``, the one entry a client cannot write; an
entry that is not an IP address (a proxy that appends a port, say) names no client, and the peer is taken. An
IPv6 client is keyed by its /64: a subscriber is given a /64 and may use any address of it, which would be
2**64 allowances. An IPv4 address mapped into IPv6 (``::ffff:192.0.2.1``) is its IPv4 address.
"""

from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from ipaddress import IPv6Address, IPv6Network, ip_address
from math import ceil
from time import monotonic
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping

from starlette.datastructures import Headers

from ledger_kernel.errors import UnavailableError
from ledger_platform.config.options import InvalidOptionError, is_positive_and_finite, require_at_least
from ledger_platform.config.settings import MAX_FORWARDED_HOPS
from ledger_platform.http.probes import FORWARDED_FOR, OPERATIONAL_PATHS, proxied
from ledger_platform.http.problems import refusal
lazy from ledger_platform.http.metrics import AsgiApp, AsgiReceive, AsgiScope, AsgiSend

_COMPONENT: Final = "rate limit"
DEFAULT_KEYS: Final = 10_000
DEFAULT_SET_ASIDE: Final = 1_000
UNKNOWN_CLIENT: Final = "unknown"
"""The key of a request whose server named no peer (no ASGI server does over TCP): one shared allowance."""
IPV6_CLIENT_PREFIX: Final = 64
"""The bits of an IPv6 address that name its client: a subscriber's /64."""


class RateLimitedError(UnavailableError):
    """A credential is over its rate: answered 429 with ``Retry-After``; retrying after that may succeed."""

    code: ClassVar[str] = "rate_limited"

    def __init__(self, wait: float) -> None:
        self.retry_after: Final = max(1, ceil(wait))
        """Whole seconds to wait before asking again."""
        super().__init__(f"Too many requests: try again in {self.retry_after} s.")

    def problem_status(self) -> HTTPStatus:
        """429 (``problems.ProblemAnswer``): its kind's 503 would blame the app, not the caller's pace."""
        return HTTPStatus.TOO_MANY_REQUESTS

    def problem_headers(self) -> Mapping[str, str]:
        """``Retry-After``: the whole seconds to wait."""
        return {"Retry-After": str(self.retry_after)}


@final
class RateLimiter:
    """GCRA per key (see the module). Not thread-safe: it belongs to one event loop."""

    __slots__ = ("_aside", "_clock", "_interval", "_max_aside", "_max_keys", "_tats", "_tolerance")

    def __init__(
        self,
        *,
        rate: float,
        burst: int,
        clock: Callable[[], float] = monotonic,
        max_keys: int = DEFAULT_KEYS,
        max_set_aside: int = DEFAULT_SET_ASIDE,
    ) -> None:
        if not is_positive_and_finite(rate):
            raise InvalidOptionError(
                component=_COMPONENT,
                option="rate",
                requirement="a positive, finite number per second",
                value=rate,
            )
        require_at_least(_COMPONENT, "burst", burst)
        require_at_least(_COMPONENT, "max_keys", max_keys)
        require_at_least(_COMPONENT, "max_set_aside", max_set_aside)
        self._clock = clock
        self._interval = 1.0 / rate
        self._tolerance = burst * self._interval
        self._max_keys = max_keys
        self._tats: OrderedDict[str, float] = OrderedDict()
        self._max_aside = max_set_aside
        self._aside: OrderedDict[str, float] = OrderedDict()
        """Active credentials made room for, with their arrival times, oldest first."""

    def wait(self, key: str) -> float:
        """Seconds ``key`` must wait before this request is allowed: ``0.0`` when it is, and it is counted."""
        now, tats = self._clock(), self._tats
        held = tats.get(key)
        if held is None:
            held = self._aside.pop(key, now)  # set aside while active: its arrival time comes back with it
            self._make_room(now)
            tats[key] = held
        ahead = max(held - now, 0.0)  # exactly zero for a credential at rest
        late = ahead + self._interval - self._tolerance
        if late > 0:
            return late  # a refusal changes nothing
        tats[key] = now + ahead + self._interval
        tats.move_to_end(key)  # the last allowed goes last: the front is the one allowed least recently
        return 0.0

    def remembered(self) -> int:
        """How many credentials it remembers."""
        return len(self._tats)

    def set_aside(self) -> int:
        """How many active credentials it set aside to make room."""
        return len(self._aside)

    def _make_room(self, now: float) -> None:
        """Room for one more: the least recently allowed, forgotten if idle, else set aside."""
        tats, aside = self._tats, self._aside
        if len(tats) < self._max_keys:
            return
        oldest, tat = tats.popitem(last=False)
        if tat > now:
            aside[oldest] = tat
            if len(aside) > self._max_aside:
                _ = aside.popitem(last=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ClientRate:
    """A rate per client address (see the module): the limiter, and the proxies in front of the app."""

    limiter: RateLimiter
    forwarded_hops: int = 0
    """How many proxies append to ``X-Forwarded-For`` in front of the app: 0 (the default) trusts no such
    header, and the peer is the client."""

    def __post_init__(self) -> None:
        if not 0 <= self.forwarded_hops <= MAX_FORWARDED_HOPS:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="forwarded_hops",
                requirement=f"0 to {MAX_FORWARDED_HOPS} proxies",
                value=self.forwarded_hops,
            )


def client_address(scope: AsgiScope, *, forwarded_hops: int) -> str:
    """The address a request came from (see the module).

    Behind ``forwarded_hops`` proxies, the entry of ``X-Forwarded-For`` (every such header, in order) that the
    outermost appended: the ``forwarded_hops``-th from the right. A chain shorter than that did not pass every
    proxy (it reached the app another way): its peer is taken. Without a peer, ``UNKNOWN_CLIENT``.
    """
    match scope.get("client"):
        case [str() as host, int()]:
            address = _client_key(host) or host  # not an IP address (a test client's name): taken as it is
        case _:
            address = UNKNOWN_CLIENT
    if forwarded_hops == 0:
        return address
    written = ",".join(Headers(scope=scope).getlist(FORWARDED_FOR)).split(",")
    chain = [entry for entry in (each.strip() for each in written) if entry]
    if len(chain) < forwarded_hops:
        return address
    return _client_key(chain[-forwarded_hops]) or address


def _client_key(written: str) -> str | None:
    """The key of the client at ``written`` (see the module); ``None`` when it is not an IP address."""
    try:
        address = ip_address(written)
    except ValueError:
        return None
    if isinstance(address, IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(IPv6Network((address, IPV6_CLIENT_PREFIX), strict=False))
    return str(address)


@final
class RateByClient:
    """Holds each client address to a ``ClientRate`` (see the module); an ASGI middleware.

    Over its rate, a request is answered 429 ``rate_limited`` with ``Retry-After``, before the app reads it.
    The probes and the metrics (``OPERATIONAL_PATHS``) asked directly are never limited: the orchestrator's
    prober and the scraper share an address with nobody, and a refused probe would take a healthy replica out
    of rotation. Asked through a proxy (``probes.proxied``) they are a client's requests like any other.
    """

    __slots__ = ("_app", "_rate")

    def __init__(self, app: AsgiApp, *, rate: ClientRate) -> None:
        self._app = app
        self._rate = rate

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        """Run the request, or refuse it when its client is over the rate."""
        if scope["type"] == "http" and (scope.get("path") not in OPERATIONAL_PATHS or proxied(scope)):
            rate = self._rate
            wait = rate.limiter.wait(client_address(scope, forwarded_hops=rate.forwarded_hops))
            if wait > 0:
                await refusal(RateLimitedError(wait))(scope, receive, send)
                return
        await self._app(scope, receive, send)
