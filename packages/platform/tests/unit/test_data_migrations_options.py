from datetime import timedelta

import pytest

from ledger_platform.config.options import InvalidOptionError
from ledger_platform.data.database import LONGEST_TIMEOUT, MILLISECOND
from ledger_platform.data.migrations import PLATFORM_MIGRATIONS

NOWHERE = "sqlite://"
"""A URL that never reaches PostgreSQL: the wait must be refused before anything is opened."""


@pytest.mark.parametrize(
    "lock_wait",
    [timedelta(0), MILLISECOND - timedelta(microseconds=1), -MILLISECOND, LONGEST_TIMEOUT + MILLISECOND],
    ids=["zero", "sub-millisecond", "negative", "beyond-32-bit-milliseconds"],
)
def test_a_lock_wait_postgresql_cannot_keep_is_refused_before_connecting(lock_wait: timedelta) -> None:
    """Zero would mean no limit to PostgreSQL, and beyond 2**31 - 1 milliseconds it refuses the setting."""
    with pytest.raises(InvalidOptionError, match=r"^migrations option lock_wait must be between") as caught:
        PLATFORM_MIGRATIONS.upgrade(NOWHERE, lock_wait=lock_wait)
    assert caught.value.component == "migrations"
