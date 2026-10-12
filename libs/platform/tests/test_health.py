import asyncio

import pytest

from ledger_platform.health import DuplicateCheckError, Health


async def healthy() -> None:
    return None


async def broken() -> None:
    raise ConnectionError


async def stuck() -> None:
    await asyncio.sleep(10)


async def test_every_check_is_reported() -> None:
    health = Health(timeout=0.05)
    health.add("database", healthy)
    health.add("nats", broken)
    health.add("slow", stuck)
    assert await health.run() == {"database": True, "nats": False, "slow": False}


async def test_checks_run_concurrently() -> None:
    """Each check waits for the other to have started: one after the other, the first would time out."""
    first, second = asyncio.Event(), asyncio.Event()

    async def meets_second() -> None:
        first.set()
        await second.wait()

    async def meets_first() -> None:
        second.set()
        await first.wait()

    health = Health(timeout=5)
    health.add("first", meets_second)
    health.add("second", meets_first)
    assert await health.run() == {"first": True, "second": True}


async def test_no_checks_is_healthy() -> None:
    assert await Health().run() == {}


def test_a_name_is_used_once() -> None:
    health = Health()
    health.add("database", healthy)
    with pytest.raises(DuplicateCheckError, match="database"):
        health.add("database", healthy)
