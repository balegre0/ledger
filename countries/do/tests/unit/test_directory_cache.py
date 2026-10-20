"""DGII's directory, kept per replica (Decision 18): 24 h an entry, 1 h a "no receiver", 10,000 at most."""

import asyncio
from typing import Final, final

import pytest

from ledger_country_do.channels.ecf_v1.adapters.dgii.directory import (
    CAPACITY,
    NEGATIVE_TTL,
    POSITIVE_TTL,
    DirectoryCache,
)
from ledger_country_do.channels.ecf_v1.application.ports import (
    AuthorityUnavailableError,
    DirectoryEntry,
    Failure,
    SigningAs,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do_testing.local_signer import SIGNING_AS
from ledger_kernel.errors import InvalidValueError
from ledger_testing.clock import Moment

T: Final = Environment.TESTECF
BUYER: Final = "131098193"
HOUR: Final = 3600.0


@final
class Directory:
    """DGII's directory as a test scripts it: who is a receiver, each question recorded."""

    def __init__(self, *receivers: str, failing: bool = False) -> None:
        self.receivers = set(receivers)
        self.failing = failing
        self.asked: list[tuple[SigningAs, Environment, str]] = []

    async def lookup(self, who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
        self.asked.append((who, environment, rnc))
        if self.failing:
            raise AuthorityUnavailableError(Failure.SERVER_ERROR, "directory")
        if rnc not in self.receivers:
            return None
        return DirectoryEntry("COMPRADOR", rnc, f"https://{rnc}.example.com", "", None)


def test_the_limits_are_decision_18_s() -> None:
    assert (POSITIVE_TTL.total_seconds(), NEGATIVE_TTL.total_seconds(), CAPACITY) == (24 * HOUR, HOUR, 10_000)


async def test_a_second_lookup_is_answered_without_asking_dgii() -> None:
    directory = Directory(BUYER)
    cache = DirectoryCache(directory.lookup, Moment())
    first = await cache.entry(SIGNING_AS, T, BUYER)
    assert first is not None
    assert await cache.entry(SIGNING_AS, T, BUYER) == first
    assert directory.asked == [(SIGNING_AS, T, BUYER)]


async def test_an_entry_is_kept_a_day_then_asked_for_again() -> None:
    directory, moment = Directory(BUYER), Moment()
    cache = DirectoryCache(directory.lookup, moment)
    await cache.entry(SIGNING_AS, T, BUYER)
    moment.now += 24 * HOUR - 1
    await cache.entry(SIGNING_AS, T, BUYER)
    moment.now += 1
    await cache.entry(SIGNING_AS, T, BUYER)
    assert len(directory.asked) == 2


async def test_no_receiver_is_kept_an_hour_then_asked_for_again() -> None:
    directory, moment = Directory(), Moment()
    cache = DirectoryCache(directory.lookup, moment)
    assert await cache.entry(SIGNING_AS, T, BUYER) is None
    moment.now += HOUR - 1
    assert await cache.entry(SIGNING_AS, T, BUYER) is None
    assert len(directory.asked) == 1
    directory.receivers.add(BUYER)  # the buyer registers: found within the hour
    moment.now += 1
    assert await cache.entry(SIGNING_AS, T, BUYER) is not None
    assert len(directory.asked) == 2


async def test_each_environment_is_its_own_and_every_company_shares_the_answers() -> None:
    directory = Directory(BUYER)
    cache = DirectoryCache(directory.lookup, Moment())
    other = SigningAs(
        organization=SIGNING_AS.organization, company=SIGNING_AS.company, mode=SIGNING_AS.mode, reason="other"
    )
    await cache.entry(SIGNING_AS, T, BUYER)
    await cache.entry(other, T, BUYER)
    await cache.entry(other, Environment.ECF, BUYER)
    assert [(environment, rnc) for _, environment, rnc in directory.asked] == [
        (T, BUYER),
        (Environment.ECF, BUYER),
    ]


async def test_a_failed_lookup_is_not_kept() -> None:
    directory = Directory(BUYER, failing=True)
    cache = DirectoryCache(directory.lookup, Moment())
    with pytest.raises(AuthorityUnavailableError):
        await cache.entry(SIGNING_AS, T, BUYER)
    directory.failing = False
    assert await cache.entry(SIGNING_AS, T, BUYER) is not None
    assert len(directory.asked) == 2


async def test_beyond_its_capacity_the_least_recently_used_answer_goes() -> None:
    directory = Directory()
    cache = DirectoryCache(directory.lookup, Moment(), capacity=2)
    for rnc in ("100000001", "100000002"):
        await cache.entry(SIGNING_AS, T, rnc)
    await cache.entry(SIGNING_AS, T, "100000001")  # used: now the most recent
    await cache.entry(SIGNING_AS, T, "100000003")  # evicts 100000002
    await cache.entry(SIGNING_AS, T, "100000001")
    await cache.entry(SIGNING_AS, T, "100000002")
    assert [rnc for _, _, rnc in directory.asked] == ["100000001", "100000002", "100000003", "100000002"]


async def test_the_default_capacity_holds_ten_thousand() -> None:
    directory = Directory()
    cache = DirectoryCache(directory.lookup, Moment())
    rncs = [f"{100000000 + each}" for each in range(CAPACITY + 1)]
    await asyncio.gather(*(cache.entry(SIGNING_AS, T, rnc) for rnc in rncs))
    await cache.entry(SIGNING_AS, T, rncs[-1])
    await cache.entry(SIGNING_AS, T, rncs[1])
    assert len(directory.asked) == CAPACITY + 1
    await cache.entry(SIGNING_AS, T, rncs[0])
    assert len(directory.asked) == CAPACITY + 2


async def test_its_own_clock_is_monotonic_time() -> None:
    directory = Directory(BUYER)
    cache = DirectoryCache(directory.lookup)
    await cache.entry(SIGNING_AS, T, BUYER)
    await cache.entry(SIGNING_AS, T, BUYER)
    assert len(directory.asked) == 1


def test_a_cache_holds_one_answer_at_least() -> None:
    with pytest.raises(InvalidValueError, match=r"directory_cache\.capacity must be at least 1"):
        DirectoryCache(Directory().lookup, capacity=0)
