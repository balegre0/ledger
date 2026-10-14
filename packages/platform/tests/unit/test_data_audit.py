import asyncio

import pytest

from ledger_platform.data.audit import Actor, MissingActorError, acting_as, current_actor

USER = Actor("user:2001", "req-1")
SYSTEM = Actor("system:purge", "job-7")


def test_no_actor_is_bound_outside_acting_as() -> None:
    with pytest.raises(MissingActorError) as raised:
        current_actor()
    assert raised.value.code == "missing_actor"


def test_acting_as_binds_the_actor_for_the_block_and_restores_the_outer_one() -> None:
    with acting_as(USER):
        assert current_actor() == USER
        with acting_as(SYSTEM):
            assert current_actor() == SYSTEM
        assert current_actor() == USER
    with pytest.raises(MissingActorError):
        current_actor()


def test_the_actor_is_restored_when_the_block_raises() -> None:
    with pytest.raises(LookupError), acting_as(USER):
        raise LookupError
    with pytest.raises(MissingActorError):
        current_actor()


async def test_an_actor_bound_in_a_task_is_not_seen_by_a_sibling_started_before_it() -> None:
    bound, looked = asyncio.Event(), asyncio.Event()

    async def sibling() -> Actor | None:
        await bound.wait()
        try:
            return current_actor()
        except MissingActorError:
            return None
        finally:
            looked.set()

    async def acting() -> None:
        with acting_as(USER):
            bound.set()
            await looked.wait()  # still bound while the sibling looks

    async with asyncio.TaskGroup() as group:
        seen = group.create_task(sibling())
        group.create_task(acting())
    assert seen.result() is None
