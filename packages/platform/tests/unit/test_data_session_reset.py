"""The pool's reset hook on a recording session (real PostgreSQL proves the effect: ``test_data_bound``)."""

from typing import Final, final

import pytest
from sqlalchemy.pool import PoolResetState

from ledger_platform.data.database import reset_session


@final
class RecordingCursor:
    def __init__(self, calls: list[str], *, failing: bool) -> None:
        self._calls = calls
        self._failing = failing

    def execute(self, operation: str, /) -> object:
        self._calls.append(operation)
        if self._failing:
            raise ConnectionError
        return None

    def close(self) -> None:
        self._calls.append("close")


@final
class RecordingSession:
    def __init__(self, *, failing: bool = False) -> None:
        self.calls: list[str] = []
        self._failing = failing

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.calls, failing=self._failing)

    def rollback(self) -> None:
        self.calls.append("rollback")

    @property
    def autocommit(self) -> bool:
        return self.calls[-1] == "autocommit=True"

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self.calls.append(f"autocommit={value}")


RETURNED: Final = PoolResetState(transaction_was_reset=False, terminate_only=False, asyncio_safe=True)


def test_a_returned_connection_forgets_its_role_and_settings_in_one_round_trip() -> None:
    session = RecordingSession()
    reset_session(session, None, RETURNED)
    assert session.calls == [
        "rollback",
        "autocommit=True",
        "RESET ROLE; RESET ALL",  # one parameterless statement: one round trip, no transaction of its own
        "close",
        "autocommit=False",
    ]


def test_a_reset_that_fails_raises_so_the_pool_invalidates_the_connection() -> None:
    session = RecordingSession(failing=True)
    with pytest.raises(ConnectionError):
        reset_session(session, None, RETURNED)
    assert session.calls == [
        "rollback",
        "autocommit=True",
        "RESET ROLE; RESET ALL",
        "close",
        "autocommit=False",
    ]


@pytest.mark.parametrize(
    "state",
    [
        PoolResetState(transaction_was_reset=False, terminate_only=True, asyncio_safe=True),
        PoolResetState(transaction_was_reset=False, terminate_only=False, asyncio_safe=False),
    ],
)
def test_a_connection_being_closed_or_collected_is_left_alone(state: PoolResetState) -> None:
    session = RecordingSession()
    reset_session(session, None, state)
    assert session.calls == []
