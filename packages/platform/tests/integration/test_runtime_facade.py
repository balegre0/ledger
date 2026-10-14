"""The facade guard on real units (§3.4, rule 13): no cross-module call while a transaction is held."""

import asyncio

import pytest

from ledger_platform.runtime.facade import UnitOpenError, facade
lazy from ledger_platform.data.bound import BoundDatabase


class Greeting:
    @facade
    async def hello(self, name: str) -> str:
        return f"hello {name}"


async def test_a_facade_answers_outside_any_unit(probe: BoundDatabase) -> None:
    del probe
    assert await Greeting().hello("x") == "hello x"


async def test_a_facade_called_while_a_unit_is_open_is_refused(probe: BoundDatabase) -> None:
    async with probe.unit(None):
        with pytest.raises(UnitOpenError, match=r"Greeting\.hello"):
            await Greeting().hello("x")


async def test_a_facade_called_while_a_purge_is_open_is_refused(probe: BoundDatabase) -> None:
    async with probe.purging():
        with pytest.raises(UnitOpenError, match="rule 13"):
            await Greeting().hello("x")


async def test_a_facade_called_outside_a_unit_and_awaited_inside_one_is_refused_when_it_starts(
    probe: BoundDatabase,
) -> None:
    """The check runs again when the coroutine first runs: a call made before a unit is not run inside it."""
    early = Greeting().hello("x")
    async with probe.unit(None):
        with pytest.raises(UnitOpenError, match=r"Greeting\.hello"):
            await early


async def test_a_task_started_inside_a_unit_may_call_after_it_closed(probe: BoundDatabase) -> None:
    gate = asyncio.Event()

    async def later() -> str:
        await gate.wait()
        return await Greeting().hello("y")

    async with probe.unit(None):
        task = asyncio.create_task(later())
    gate.set()
    assert await task == "hello y"


def test_a_facade_keeps_its_methods_name() -> None:
    assert Greeting.hello.__qualname__ == "Greeting.hello"
    assert Greeting.hello.__name__ == "hello"
