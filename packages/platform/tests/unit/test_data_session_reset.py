"""The pool's reset hook on a recording session (real PostgreSQL proves the effect: ``test_data_bound``)."""

from typing import final

import pytest
from sqlalchemy.pool import PoolResetState

from ledger_platform.data.database import reset_session


@final
class RecordingCursor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def execute(self, operation: str, /) -> object:
        self._calls.append(operation)
        return None

    def close(self) -> None:
        self._calls.append("close")


@final
class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.calls)

    def rollback(self) -> None:
        self.calls.append("rollback")

    def commit(self) -> None:
        self.calls.append("commit")


def test_a_returned_connection_forgets_its_role_and_settings_in_a_transaction_of_its_own() -> None:
    session = RecordingSession()
    reset_session(
        session, None, PoolResetState(transaction_was_reset=False, terminate_only=False, asyncio_safe=True)
    )
    assert session.calls == ["rollback", "RESET ROLE", "RESET ALL", "close", "commit"]


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
