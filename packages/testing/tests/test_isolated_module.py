"""The module fixtures of a module isolated in the signer's role (``vault``, the conftest's probe)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from ledger_platform.data.bound import BoundDatabase
from ledger_testing.modules import QuietSignals
lazy from ledger_platform.data.database import Database
lazy from ledger_testing.modules import ModuleUnderTest


@pytest.fixture
def module_under_test(isolated_under_test: ModuleUnderTest) -> ModuleUnderTest:
    """This file's suite is the isolated module's."""
    return isolated_under_test


async def test_module_database_of_an_isolated_module_is_the_signer_login(
    module_database: BoundDatabase,
) -> None:
    async with module_database.unit(None) as unit:
        found = await unit.connection.execute(text("SELECT session_user, current_user"))
        assert tuple(found.one()) == ("ledger_signer", "vault_rw")


async def test_the_login_of_every_other_app_cannot_enter_an_isolated_schema(app_database: Database) -> None:
    bound = BoundDatabase(
        app_database, schema="vault", source="vault", signals=QuietSignals(), publishes=frozenset()
    )
    with pytest.raises(ProgrammingError, match="permission denied to set role"):
        async with bound.unit(None):
            pass
