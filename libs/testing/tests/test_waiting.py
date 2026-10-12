import asyncio

import psycopg
import pytest

from ledger_testing.containers import libpq_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_testing.containers import Postgres


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


async def test_a_session_held_behind_a_lock_is_seen_waiting(server: Postgres) -> None:
    url = server.database("locks", role="locks_owner", password="locks-password")
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
