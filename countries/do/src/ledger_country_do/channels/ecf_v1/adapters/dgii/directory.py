"""DGII's directory of electronic receivers, cached in the process (Decision 18).

A buyer's directory entry changes rarely and is asked for every delivery, so each replica keeps what DGII
answered: an entry for ``POSITIVE_TTL`` (24 h), DGII's "no such receiver" for ``NEGATIVE_TTL`` (1 h, so a
buyer that registers is found within the hour), at most ``CAPACITY`` (10,000) answers, the least recently
used dropped first. Keys are (environment, RNC): the directory is DGII's, the same for every issuing company,
so the cache is shared by all of them (and not kept in PostgreSQL, where a table keyed by RNC across
organizations would break the shard key, spec §6.6). A failed lookup is not cached: it raises, and the next
asks DGII again. Ages are measured on a monotonic clock (``clock``): a wall clock that jumps neither keeps a
stale entry nor drops a fresh one.
"""

import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, final
lazy from collections.abc import Awaitable, Callable

from ledger_kernel.errors import InvalidValueError
lazy from ledger_country_do.channels.ecf_v1.application.ports import DirectoryEntry, SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_kernel.clock import MonotonicClock

POSITIVE_TTL: Final = timedelta(hours=24)
NEGATIVE_TTL: Final = timedelta(hours=1)
CAPACITY: Final = 10_000

type DirectoryLookup = Callable[[SigningAs, Environment, str], Awaitable[DirectoryEntry | None]]
"""Who is asking, in which environment, about which RNC: DGII's entry, or ``None`` (no receiver)."""


@final
@dataclass(frozen=True, slots=True)
class _Kept:
    entry: DirectoryEntry | None
    until: float


@final
class DirectoryCache:
    """``lookup``'s answers, kept as the module says; ``entry`` has ``lookup``'s shape."""

    __slots__ = ("_capacity", "_clock", "_kept", "_lookup")

    def __init__(
        self, lookup: DirectoryLookup, clock: MonotonicClock = time.monotonic, *, capacity: int = CAPACITY
    ) -> None:
        if capacity < 1:
            raise InvalidValueError(field="directory_cache.capacity", requirement="at least 1")
        self._lookup = lookup
        self._clock = clock
        self._capacity = capacity
        self._kept: OrderedDict[tuple[Environment, str], _Kept] = OrderedDict()

    async def entry(self, who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
        """``rnc``'s entry in ``environment``: kept, or asked of DGII as ``who`` and kept."""
        key = (environment, rnc)
        kept = self._kept.get(key)
        if kept is not None and kept.until > self._clock():
            self._kept.move_to_end(key)
            return kept.entry
        found = await self._lookup(who, environment, rnc)
        ttl = NEGATIVE_TTL if found is None else POSITIVE_TTL
        self._kept[key] = _Kept(found, self._clock() + ttl.total_seconds())
        self._kept.move_to_end(key)
        while len(self._kept) > self._capacity:
            self._kept.popitem(last=False)
        return found
