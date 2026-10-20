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
"""

from collections import OrderedDict
from http import HTTPStatus
from math import ceil
from time import monotonic
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping

from ledger_kernel.errors import UnavailableError
from ledger_platform.config.options import InvalidOptionError, is_positive_and_finite, require_at_least

_COMPONENT: Final = "rate limit"
DEFAULT_KEYS: Final = 10_000
DEFAULT_SET_ASIDE: Final = 1_000


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
