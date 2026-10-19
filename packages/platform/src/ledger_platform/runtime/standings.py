"""How callers stand, as a module provides it to the chassis (§9.7): the port's key, and a cache of it.

A module (identity) hands over its ``StandingsPort`` under ``STANDINGS_PORT`` and declares it in its manifest
(``[extension_points] provides = ["StandingsPort"]``); the assembly binds it to the app's ``Authentication``.
What other packages call is a ``@facade``: so is ``CachedStandings.of``, which opens units of its own.
"""

from collections import OrderedDict
from time import monotonic
from typing import Final, TypeIs, final, override

from ledger_platform.config.options import require_at_least, require_seconds
from ledger_platform.http.standing import NOTHING_KNOWN, StandingsPort
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.keys import Key
lazy from ledger_kernel.access import Principal, SubjectId
lazy from ledger_kernel.clock import MonotonicClock
lazy from ledger_kernel.ids import OrganizationId
lazy from ledger_platform.http.standing import Standing


def _is_standings_port(value: object) -> TypeIs[StandingsPort]:
    return isinstance(value, StandingsPort)


STANDINGS_PORT: Final = Key("StandingsPort", _is_standings_port)
"""The platform's port under its name: how a module provides it and the app binds it."""


@final
class CachedStandings(StandingsPort):
    """A ``StandingsPort``'s answers, kept ``ttl`` seconds for ``size`` callers at most, oldest out first.

    Every request asks for the standing of its caller, and it changes rarely (a pin is fixed when its owner is
    made; an API client is registered or revoked now and then): this keeps the question off the database. A
    change shows within ``ttl``: a revoked client works that long. Nothing is remembered of a question that
    failed, nor of an answer that knows nothing: it is what a caller of an organization that does not exist
    yet gets, and its next question must see the organization once it does.
    """

    __slots__ = ("_clock", "_inner", "_kept", "_size", "_ttl")

    def __init__(
        self, inner: StandingsPort, *, ttl: float, size: int, clock: MonotonicClock = monotonic
    ) -> None:
        require_seconds("standing", "ttl", ttl)
        require_at_least("standing", "size", size)
        self._inner = inner
        self._ttl = ttl
        self._size = size
        self._clock = clock
        self._kept: OrderedDict[tuple[OrganizationId, SubjectId], tuple[float, Standing]] = OrderedDict()

    @override
    @facade
    async def of(self, principal: Principal) -> Standing:
        key, now = (principal.organization, principal.subject), self._clock()
        held = self._kept.get(key)
        if held is not None and now < held[0]:
            return held[1]
        standing = await self._inner.of(principal)
        _ = self._kept.pop(key, None)
        if standing != NOTHING_KNOWN:
            self._kept[key] = (now + self._ttl, standing)
            while len(self._kept) > self._size:
                _ = self._kept.popitem(last=False)
        return standing
