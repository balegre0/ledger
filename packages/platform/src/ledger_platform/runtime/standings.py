"""How callers stand, as a module provides it to the chassis (§9.7): the port's key, and a cache of it.

A module (identity) hands over its ``StandingsPort`` under ``STANDINGS_PORT`` and declares it in its manifest
(``[extension_points] provides = ["StandingsPort"]``); the assembly binds it to the app's ``Authentication``.
What other packages call is a ``@facade``: so is ``CachedStandings.of``, which opens units of its own.
"""

import asyncio
from collections import OrderedDict
from functools import partial
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
NEGATIVE_TTL: Final = 5.0
"""Seconds an answer that knows nothing is kept: what a new organization or API client waits at most."""
type _Caller = tuple[OrganizationId, SubjectId]


@final
class CachedStandings(StandingsPort):
    """A ``StandingsPort``'s answers, kept ``ttl`` seconds for ``size`` callers at most, oldest out first.

    Every request asks for the standing of its caller, and it changes rarely (a pin is fixed when its owner is
    made; an API client is registered or revoked now and then): this keeps the question off the database. A
    change shows within ``ttl``: a revoked client works that long on each replica that remembers it. An answer
    that knows nothing (a caller of an organization identity has not seen yet, a machine that is no API client
    yet) is kept ``negative_ttl`` seconds only: the organization or the client it waits for shows that soon.
    One question is asked at a time per caller: the requests that arrive while it is out wait for its answer
    (and a request that gives up does not stop it for the others). Nothing is kept of a question that failed:
    each request waiting for it gets the failure, and the next asks again.
    """

    __slots__ = ("_clock", "_flights", "_inner", "_kept", "_negative_ttl", "_size", "_ttl")

    def __init__(
        self,
        inner: StandingsPort,
        *,
        ttl: float,
        size: int,
        negative_ttl: float = NEGATIVE_TTL,
        clock: MonotonicClock = monotonic,
    ) -> None:
        require_seconds("standing", "ttl", ttl)
        require_seconds("standing", "negative_ttl", negative_ttl)
        require_at_least("standing", "size", size)
        self._inner = inner
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._size = size
        self._clock = clock
        self._kept: OrderedDict[_Caller, tuple[float, Standing]] = OrderedDict()
        self._flights: dict[_Caller, asyncio.Future[Standing]] = {}

    @override
    @facade
    async def of(self, principal: Principal) -> Standing:
        key = (principal.organization, principal.subject)
        held = self._kept.get(key)
        if held is not None and self._clock() < held[0]:
            return held[1]
        flight = self._flights.get(key)
        if flight is None:
            flight = self._flights[key] = asyncio.ensure_future(self._ask(key, principal))
            flight.add_done_callback(partial(self._landed, key))
        return await asyncio.shield(flight)

    async def _ask(self, key: _Caller, principal: Principal) -> Standing:
        """Ask the inner port, and keep its answer: ``negative_ttl`` when it knows nothing, else ``ttl``."""
        standing = await self._inner.of(principal)
        ttl = self._negative_ttl if standing == NOTHING_KNOWN else self._ttl
        _ = self._kept.pop(key, None)
        self._kept[key] = (self._clock() + ttl, standing)
        while len(self._kept) > self._size:
            _ = self._kept.popitem(last=False)
        return standing

    def _landed(self, key: _Caller, flight: asyncio.Future[Standing]) -> None:
        """The question is answered (or failed): the next one is asked anew; its failure counts as seen."""
        del self._flights[key]
        _ = flight.cancelled() or flight.exception()
