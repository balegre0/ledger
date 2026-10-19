"""Review Focus 1: no login but the signer's reaches signer's schema (spec §6.2, §10, ADR 0010).

``ledger_app`` is the login of every app but the signer: neither a bare session of it nor a unit an app opens
with it (in signer's role, as the api's assembly binds the relayed module, or in another module's) may read a
certificate or its sealed material. ``ledger_signer``, the signer's login, may.
"""

from typing import Final

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import func, select, table
from sqlalchemy.exc import DBAPIError
lazy from sqlalchemy import Select

from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.data.bound import BoundDatabase
from ledger_testing.modules import QuietSignals
lazy from ledger_platform.data.database import Database

COMPANY: Final = Tenant(organization=OrganizationId(new_id()), company=CompanyId(new_id()), mode=None)
TABLES: Final = ("certificates", "custody")


def refused(caught: pytest.ExceptionInfo[DBAPIError]) -> bool:
    return isinstance(caught.value.orig, InsufficientPrivilege)


def count(name: str) -> Select[int]:
    """How many rows ``signer.<name>`` holds."""
    return select(func.count()).select_from(table(name, schema="signer"))


def bound(database: Database, schema: str) -> BoundDatabase:
    """``schema``'s units over ``database``, as an app binds them."""
    return BoundDatabase(database, schema=schema, source=schema, signals=QuietSignals())


@pytest.mark.parametrize("name", TABLES)
async def test_the_apps_login_cannot_read_the_signer_s_tables(app_database: Database, name: str) -> None:
    with pytest.raises(DBAPIError) as caught:
        async with app_database.engine.connect() as connection:
            await connection.execute(count(name))
    assert refused(caught)


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("schema", ["signer", "identity"])
async def test_not_even_inside_an_api_unit(app_database: Database, name: str, schema: str) -> None:
    """In signer's role (the api binds the relayed module's schema) or in another module's."""
    with pytest.raises(DBAPIError) as caught:
        async with bound(app_database, schema).unit(COMPANY) as unit:
            await unit.connection.execute(count(name))
    assert refused(caught)


@pytest.mark.parametrize("name", TABLES)
async def test_the_signer_s_login_reads_them(module_database: BoundDatabase, name: str) -> None:
    async with module_database.unit(COMPANY) as unit:
        seen = (await unit.connection.execute(count(name))).scalar_one()
    assert seen == 0
