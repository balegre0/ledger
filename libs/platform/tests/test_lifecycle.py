from contextlib import asynccontextmanager
lazy from collections.abc import AsyncGenerator
lazy from contextlib import AbstractAsyncContextManager

import pytest

from ledger_platform.lifecycle import Lifecycle


def resource(name: str, log: list[str]) -> AbstractAsyncContextManager[str]:
    @asynccontextmanager
    async def managed() -> AsyncGenerator[str]:
        log.append(f"open {name}")
        try:
            yield name
        finally:
            log.append(f"close {name}")

    return managed()


async def test_resources_close_in_reverse_order() -> None:
    log: list[str] = []

    async def flush() -> None:
        log.append("flush")

    async with Lifecycle() as lifecycle:
        assert await lifecycle.enter(resource("pool", log)) == "pool"
        lifecycle.on_close(flush)
        await lifecycle.enter(resource("client", log))
    assert log == ["open pool", "open client", "close client", "flush", "close pool"]


def fail() -> None:
    raise RuntimeError


async def start_and_fail(log: list[str]) -> None:
    async with Lifecycle() as lifecycle:
        await lifecycle.enter(resource("pool", log))
        fail()


async def test_a_failed_start_still_releases_what_was_opened() -> None:
    log: list[str] = []
    with pytest.raises(RuntimeError):
        await start_and_fail(log)
    assert log == ["open pool", "close pool"]


async def explode() -> None:
    raise RuntimeError


async def open_then_fail_to_close(log: list[str]) -> None:
    async with Lifecycle() as lifecycle:
        await lifecycle.enter(resource("pool", log))
        lifecycle.on_close(explode)


async def test_a_failing_release_does_not_stop_the_others() -> None:
    log: list[str] = []
    with pytest.raises(RuntimeError):
        await open_then_fail_to_close(log)
    assert log == ["open pool", "close pool"]
