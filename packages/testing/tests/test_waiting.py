import asyncio
import gc
import weakref

import psycopg
import pytest

from ledger_testing.containers import libpq_url
from ledger_testing.waiting import NoTaskError, Watched, eventually, waits_for_a_lock
lazy from ledger_testing.postgres import PostgresServer


async def test_waiting_ends_as_soon_as_the_check_holds() -> None:
    answers = iter((False, False, True, False))

    async def check() -> bool:
        return next(answers)

    await eventually(check)
    assert next(answers) is False  # asked three times, not once more


async def test_a_check_that_never_holds_ends_in_a_timeout() -> None:
    async def never() -> bool:
        return False

    with pytest.raises(TimeoutError):
        await eventually(never, within=0.05)


async def test_a_session_held_behind_a_lock_is_seen_waiting(postgres: PostgresServer) -> None:
    url = postgres.database("locks", role="locks_owner", password="locks-password")
    assert not await waits_for_a_lock(url)
    async with (
        await psycopg.AsyncConnection.connect(libpq_url(url)) as holder,
        await psycopg.AsyncConnection.connect(libpq_url(url), autocommit=True) as waiter,
    ):
        await holder.execute("CREATE TABLE gate (id integer)")
        await holder.commit()
        await holder.execute("LOCK TABLE gate IN ACCESS EXCLUSIVE MODE")
        blocked = asyncio.create_task(waiter.execute("SELECT id FROM gate"))
        await eventually(lambda: waits_for_a_lock(url))
        await holder.rollback()
        await blocked
    assert not await waits_for_a_lock(url)


async def test_a_watched_task_has_ended_and_its_earlier_callbacks_have_run_once_the_wait_returns() -> None:
    """What code under test hooked on a task's end (a done callback) has run when ``ended`` returns."""
    watched, started, release, seen = Watched(), asyncio.Event(), asyncio.Event(), list[str]()

    async def work() -> None:
        watched.enter()
        started.set()
        await release.wait()

    task = asyncio.create_task(work())
    await started.wait()
    task.add_done_callback(lambda _: seen.append("callback"))
    release.set()
    await watched.ended()
    assert (task.done(), seen) == (True, ["callback"])
    gone = weakref.ref(task)
    del task
    gc.collect()
    assert gone() is None, "an ended task is forgotten, and what it raised with it"


async def test_a_task_is_entered_from_inside_a_task_only() -> None:
    entered = asyncio.get_running_loop().create_future()

    def outside_any_task() -> None:
        try:
            Watched().enter()
        except NoTaskError as error:
            entered.set_result(str(error))

    asyncio.get_running_loop().call_soon(outside_any_task)
    assert await entered == "Watched.enter is called from the task to watch, not from a callback"
