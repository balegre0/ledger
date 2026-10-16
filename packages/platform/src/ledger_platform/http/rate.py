"""A rate limit per credential, in process (§10): the backstop behind the ingress's own.

GCRA (the generic cell rate algorithm) keeps one number per credential, the time its next request would be due
if it went at the steady rate (its theoretical arrival time). A request is allowed while that time is no more
than a burst's worth ahead of now; one that is not allowed is told how long to wait and uses up nothing, so
asking again earns no further delay. The limiter is per replica: a credential may use the allowance of each
replica it reaches, which is what a backstop for a runaway caller needs.

It remembers a bounded number of credentials: those idle for a whole burst are forgotten first, and when all
are active the oldest is, so a flood of credentials cannot grow it.
"""

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
        self._tats: dict[str, float] = {}

    def wait(self, key: str) -> float:
        """Seconds ``key`` must wait before this request is allowed: ``0.0`` when it is, and it is counted."""
        now = self._clock()
        due = max(self._tats.get(key, now), now) + self._interval
        late = due - now - self._tolerance
        if late > 0:
            return late  # a refusal changes nothing
        _ = self._tats.pop(key, None)  # put back last: the oldest is the one allowed least recently
        self._make_room(now)
        self._tats[key] = due
        return 0.0

    def remembered(self) -> int:
        """How many credentials it remembers."""
        return len(self._tats)

    def _make_room(self, now: float) -> None:
        """Forget the idle credentials, and the oldest if all are active, until one more fits."""
        if len(self._tats) < self._max_keys:
            return
        for key in [key for key, tat in self._tats.items() if tat <= now]:
            del self._tats[key]
        while len(self._tats) >= self._max_keys:
            del self._tats[next(iter(self._tats))]
