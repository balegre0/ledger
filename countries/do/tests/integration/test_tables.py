"""The channel's tables as migrated: a company's unit in a mode sees its own rows, and nothing else.

Row-level security denies by default, table by table: a unit without a scope, an organization's unit, another
company's or the other mode's sees none of a tenant's rows, and none can write a row it does not own. The
constraints hold whatever the repositories do.
"""

from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID
lazy from collections.abc import Callable

import pytest
from psycopg.errors import CheckViolation, ExclusionViolation, ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import (
    NumberingRangeRecord,
    NumberingSeriesRecord,
    ReleasedNumberRecord,
)
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DoEcfTable
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 20, 12, tzinfo=UTC)
RANGE: Final = UUID("0199e3a0-0000-7000-8000-0000000000a1")
SERIES: Final = {"environment": "testecf", "ecf_type": "31"}


def a_tenant(organization: OrganizationId | None = None) -> Tenant:
    return Tenant(organization or OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


def owned(tenant: Tenant) -> dict[str, object]:
    return {"organization_id": tenant.organization, "company_id": tenant.company, "mode": "test"}


def series(tenant: Tenant) -> dict[str, object]:
    return owned(tenant) | SERIES | {"version": 1}


def a_range(tenant: Tenant, **changes: object) -> dict[str, object]:
    row: dict[str, object] = (
        owned(tenant)
        | SERIES
        | {
            "id": RANGE,
            "first": 1,
            "last": 100,
            "next": 1,
            "expires_on": date(2028, 12, 31),
            "status": "active",
            "created_at": NOW,
            "version": 1,
        }
    )
    return row | changes


def released(tenant: Tenant, sequence: int = 1) -> dict[str, object]:
    return (
        owned(tenant)
        | SERIES
        | {
            "sequence": sequence,
            "released_at": NOW,
            "available_at": NOW,
            "document_id": new_id(),
        }
    )


ROWS: Final[dict[str, tuple[type[DoEcfTable], Callable[[Tenant], dict[str, object]]]]] = {
    "numbering_series": (NumberingSeriesRecord, series),
    "numbering_ranges": (NumberingRangeRecord, a_range),
    "released_numbers": (ReleasedNumberRecord, released),
}


async def kept(module_database: BoundDatabase, tenant: Tenant) -> None:
    """One row of every table for ``tenant``."""
    async with module_database.unit(tenant) as unit:
        for name, (_, row) in ROWS.items():
            await unit.connection.execute(insert(ROWS[name][0]).values(row(tenant)))


async def inserted(
    module_database: BoundDatabase, tenant: Tenant, rows: list[tuple[type[DoEcfTable], dict[str, object]]]
) -> None:
    """``rows`` inserted in one unit of ``tenant``, in order."""
    async with module_database.unit(tenant) as unit:
        for table, row in rows:
            await unit.connection.execute(insert(table).values(row))


async def count(module_database: BoundDatabase, scope: Scope | None, name: str) -> int:
    async with module_database.unit(scope) as unit:
        found = await unit.connection.execute(select(func.count()).select_from(ROWS[name][0]))
        return found.scalar_one()


@pytest.mark.parametrize("name", sorted(ROWS))
async def test_a_tenant_s_unit_sees_its_own_rows_and_nothing_else(
    module_database: BoundDatabase, name: str
) -> None:
    mine = a_tenant()
    await kept(module_database, mine)
    await kept(module_database, a_tenant(mine.organization))  # a sibling company's
    assert await count(module_database, mine, name) == 1
    assert await count(module_database, Tenant(mine.organization, mine.company, Mode.LIVE), name) == 0
    assert await count(module_database, Tenant(mine.organization, mine.company, None), name) == 0
    assert await count(module_database, OrganizationScope(mine.organization, Mode.TEST), name) == 0
    assert await count(module_database, a_tenant(), name) == 0
    assert await count(module_database, None, name) == 0


@pytest.mark.parametrize("name", sorted(ROWS))
@pytest.mark.parametrize("scope", [None, "another company", "the other mode"])
async def test_a_unit_cannot_write_a_row_of_another_tenant(
    module_database: BoundDatabase, name: str, scope: str | None
) -> None:
    victim = a_tenant()
    units: dict[str | None, Scope | None] = {
        None: None,
        "another company": a_tenant(),
        "the other mode": Tenant(victim.organization, victim.company, Mode.LIVE),
    }
    with pytest.raises(ProgrammingError) as caught:
        async with module_database.unit(units[scope]) as unit:
            await unit.connection.execute(insert(ROWS[name][0]).values(ROWS[name][1](victim)))
    assert isinstance(caught.value.orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    "change",
    [
        {"first": 0},
        {"next": 102},
        {"last": 10_000_000_000},
        {"status": "spent"},
        {"environment": "production"},
        {"ecf_type": "30"},
        {"version": 0},
    ],
)
async def test_a_range_s_numbers_and_words_are_checked(
    module_database: BoundDatabase, change: dict[str, object]
) -> None:
    tenant = a_tenant()
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(NumberingSeriesRecord).values(series(tenant)))
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(insert(NumberingRangeRecord).values(a_range(tenant, **change)))
    assert isinstance(caught.value.orig, CheckViolation)


@pytest.mark.parametrize("name", sorted(ROWS))
@pytest.mark.parametrize(
    ("mode", "environment"), [(Mode.TEST, "ecf"), (Mode.LIVE, "testecf"), (Mode.LIVE, "certecf")]
)
async def test_a_live_row_is_of_the_live_environment_and_a_test_row_of_a_test_one(
    module_database: BoundDatabase, name: str, mode: Mode, environment: str
) -> None:
    """``ecf`` serves live documents only, ``testecf`` and ``certecf`` test ones (``ChannelDef.mode_of``)."""
    organization, company = OrganizationId(new_id()), CompanyId(new_id())
    tenant = Tenant(organization, company, mode)
    placed = {"mode": mode.value, "environment": environment}
    rows = [(ROWS[name][0], ROWS[name][1](tenant) | placed)]
    if name != "numbering_series":  # its series first, as placed
        rows.insert(0, (NumberingSeriesRecord, series(tenant) | placed))
    with pytest.raises(IntegrityError) as caught:
        await inserted(module_database, tenant, rows)
    assert isinstance(caught.value.orig, CheckViolation)


@pytest.mark.parametrize(
    "change",
    [{"next": 4}, {"first": 2}, {"last": 99}, {"ecf_type": "32"}, {"environment": "certecf"}],
)
async def test_a_range_never_gives_a_number_back_nor_changes_what_dgii_authorized(
    module_database: BoundDatabase, change: dict[str, object]
) -> None:
    """Whatever the repositories do: ``next`` only moves on, the authorization is kept as registered."""
    tenant = a_tenant()
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(NumberingSeriesRecord).values(series(tenant)))
        await unit.connection.execute(insert(NumberingRangeRecord).values(a_range(tenant, next=5)))
    with pytest.raises(IntegrityError, match="numbering_range_frozen") as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(update(NumberingRangeRecord).values(**change))
    assert isinstance(caught.value.orig, CheckViolation)
    async with module_database.unit(tenant) as unit:  # moving on, or voiding, is what a range does
        await unit.connection.execute(update(NumberingRangeRecord).values(next=6, status="voided", version=2))


async def test_no_two_ranges_of_a_series_share_a_number_whatever_the_repositories_do(
    module_database: BoundDatabase,
) -> None:
    """The series' lock keeps registrations apart; the table refuses an overlap all the same (a trigger:
    ``btree_gist``, which an exclusion constraint would need, is no extension the migrator may create)."""
    tenant = a_tenant()
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(NumberingSeriesRecord).values(series(tenant)))
        await unit.connection.execute(insert(NumberingRangeRecord).values(a_range(tenant)))
        other_type = series(tenant) | {"ecf_type": "32"}
        await unit.connection.execute(insert(NumberingSeriesRecord).values(other_type))
        await unit.connection.execute(
            insert(NumberingRangeRecord).values(a_range(tenant, id=new_id(), ecf_type="32"))
        )
    shared = a_range(tenant, id=new_id(), first=100, last=200, next=100)
    with pytest.raises(IntegrityError, match="numbering_range_overlaps") as caught:
        await inserted(module_database, tenant, [(NumberingRangeRecord, shared)])
    assert isinstance(caught.value.orig, ExclusionViolation)
    async with module_database.unit(tenant) as unit:
        after = a_range(tenant, id=new_id(), first=101, last=200, next=101)
        await unit.connection.execute(insert(NumberingRangeRecord).values(after))


async def test_a_range_belongs_to_a_series(module_database: BoundDatabase) -> None:
    """Its series' row is what registration and allocation lock: no range exists without one."""
    tenant = a_tenant()
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(insert(NumberingRangeRecord).values(a_range(tenant)))
    assert isinstance(caught.value.orig, ForeignKeyViolation)


async def test_a_unit_without_a_company_in_a_mode_has_no_channel_view(module_database: BoundDatabase) -> None:
    tenant = a_tenant()
    for scope in (None, Tenant(tenant.organization, tenant.company, None)):
        async with module_database.unit(scope) as unit:
            with pytest.raises(InvalidValueError, match=r"unit\.scope"):
                bind(unit)
