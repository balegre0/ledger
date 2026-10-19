"""A rate limit per credential, in process (§10): the backstop behind the ingress's own.

GCRA (the generic cell rate algorithm) keeps one number per credential, the time its next request would be due
if it went at the steady rate (its theoretical arrival time). A request is allowed while that time is no more
than a burst's worth ahead of now; one that is not allowed is told how long to wait and uses up nothing, so
asking again earns no further delay. The limiter is per replica: a credential may use the allowance of each
replica it reaches, which is what a backstop for a runaway caller needs.

It remembers a bounded number of credentials (``max_keys``), in the order they were last allowed. Room for a
new one is made by forgetting the least recently allowed while it is idle (it would have its whole burst back
anyway), one at a time from the front: constant time per request. When every credential it remembers is active
(each was allowed within its last burst), the new one is refused until the oldest goes idle: forgetting an
active one would hand it a fresh burst, so a flood of credentials can neither grow it nor reset an allowance.
The settings are ``LEDGER_RATE_LIMIT_PER_SECOND`` and ``LEDGER_RATE_LIMIT_BURST``.
"""

from collections import OrderedDict
from math import ceil
from time import monotonic
from typing import ClassVar, Final, final
lazy from collections.abc import Callable

from ledger_kernel.errors import UnavailableError
from ledger_platform.config.options import InvalidOptionError, is_positive_and_finite, require_at_least

_COMPONENT: Final = "rate limit"
DEFAULT_KEYS: Final = 10_000


class RateLimitedError(UnavailableError):
    """A credential is over its rate: answered 429 with ``Retry-After``; retrying after that may succeed."""

    code: ClassVar[str] = "rate_limited"

    def __init__(self, wait: float) -> None:
        self.retry_after: Final = max(1, ceil(wait))
        """Whole seconds to wait before asking again."""
        super().__init__(f"Too many requests: try again in {self.retry_after} s.")


@final
class RateLimiter:
    """GCRA per key (see the module). Not thread-safe: it belongs to one event loop."""

    __slots__ = ("_clock", "_interval", "_max_keys", "_tats", "_tolerance")

    def __init__(
        self,
        *,
        rate: float,
        burst: int,
        clock: Callable[[], float] = monotonic,
        max_keys: int = DEFAULT_KEYS,
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
        self._clock = clock
        self._interval = 1.0 / rate
        self._tolerance = burst * self._interval
        self._max_keys = max_keys
        self._tats: OrderedDict[str, float] = OrderedDict()

    def wait(self, key: str) -> float:
        """Seconds ``key`` must wait before this request is allowed: ``0.0`` when it is, and it is counted."""
        now, tats = self._clock(), self._tats
        held = tats.get(key)
        if held is None and (full := self._room(now)) > 0:
            return full  # every credential remembered is active: this one waits for room
        ahead = max((now if held is None else held) - now, 0.0)  # exactly zero for a credential at rest
        late = ahead + self._interval - self._tolerance
        if late > 0:
            return late  # a refusal changes nothing
        tats[key] = now + ahead + self._interval
        tats.move_to_end(key)  # the last allowed goes last: the front is the one allowed least recently
        return 0.0

    def remembered(self) -> int:
        """How many credentials it remembers."""
        return len(self._tats)

    def _room(self, now: float) -> float:
        """Make room for one more credential: ``0.0`` once there is, else seconds until the oldest is idle.

        The least recently allowed are forgotten from the front while they are idle (their theoretical arrival
        is past); an active one is never forgotten.
        """
        tats = self._tats
        while len(tats) >= self._max_keys:
            oldest, tat = next(iter(tats.items()))
            if tat > now:
                return tat - now
            del tats[oldest]
        return 0.0
