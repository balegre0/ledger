"""Number allocation in PostgreSQL (§8.6.3; Decision 9): under the series' row lock, released numbers first.

Each allocation runs in a unit of its own, as each document is numbered in the unit that records it: the
series' row serializes them, so concurrent units get distinct numbers, with no gap. A released number is taken
only once it is available, and then it is gone.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import insert, select, text
from sqlalchemy.exc import OperationalError

from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import (
    NumberingRangeRecord,
    ReleasedNumberRecord,
)
from ledger_country_do.channels.ecf_v1.adapters.postgres.unit import bind
from ledger_country_do.channels.ecf_v1.application.ranges import NumberingRange
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import (
    Allocation,
    NoNumberAvailableError,
    RangeState,
    RangeStatus,
    release_at,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 20, 15, tzinfo=UTC)
T31: Final = EcfType.CREDIT_INVOICE
TESTECF: Final = Environment.TESTECF
RANGE: Final = UUID("0199e3a0-0000-7000-8000-0000000000a1")
CONCURRENT: Final = 50


def a_tenant() -> Tenant:
    return Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)


async def with_range(
    module_database: BoundDatabase, tenant: Tenant, *, first: int = 1, last: int = 100
) -> None:
    async with module_database.unit(tenant) as unit:
        ranges = bind(unit).ranges
        await ranges.series(TESTECF, T31)
        authorized = RangeState(
            id=RANGE,
            ecf_type=T31,
            first=first,
            last=last,
            next=first,
            expires_on=date(2028, 12, 31),
            status=RangeStatus.ACTIVE,
        )
        await ranges.add(NumberingRange(environment=TESTECF, range=authorized, created_at=NOW, version=1))


async def allocated(module_database: BoundDatabase, tenant: Tenant, now: datetime = NOW) -> Allocation:
    async with module_database.unit(tenant) as unit:
        return await bind(unit).ranges.allocate(TESTECF, T31, now=now)


async def test_concurrent_allocations_on_one_series_get_distinct_numbers_with_no_gap(
    module_database: BoundDatabase,
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    allocations = await asyncio.gather(*(allocated(module_database, tenant) for _ in range(CONCURRENT)))
    assert sorted(each.encf.sequence for each in allocations) == list(range(1, CONCURRENT + 1))
    assert {(each.range_id, each.released, each.expires_on) for each in allocations} == {
        (RANGE, False, date(2028, 12, 31))
    }
    async with module_database.unit(tenant) as unit:
        found = await unit.connection.execute(select(NumberingRangeRecord.next, NumberingRangeRecord.version))
        assert found.one() == (CONCURRENT + 1, CONCURRENT + 1)  # each allocation a version of the range


async def test_a_released_number_is_taken_once_available_and_never_again(
    module_database: BoundDatabase,
) -> None:
    tenant = a_tenant()
    await with_range(module_database, tenant)
    assert (await allocated(module_database, tenant)).encf == Encf(T31, 1)
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(
            insert(ReleasedNumberRecord).values(
                organization_id=tenant.organization,
                company_id=tenant.company,
                mode="test",
                environment="testecf",
                ecf_type="31",
                sequence=1,
                released_at=NOW,
                available_at=release_at(NOW),
                document_id=new_id(),
            )
        )
    early = await allocated(module_database, tenant, NOW + timedelta(minutes=1))
    assert (early.encf, early.released) == (Encf(T31, 2), False)  # its submission may still be listed
    later = NOW + timedelta(minutes=3)
    assert await allocated(module_database, tenant, later) == Allocation(
        encf=Encf(T31, 1), range_id=RANGE, expires_on=date(2028, 12, 31), released=True
    )
    assert (await allocated(module_database, tenant, later)).encf == Encf(T31, 3)


async def test_a_series_without_a_usable_range_numbers_nothing(module_database: BoundDatabase) -> None:
    tenant = a_tenant()
    with pytest.raises(NoNumberAvailableError):
        await allocated(module_database, tenant)
    await with_range(module_database, tenant, first=7, last=7)
    assert (await allocated(module_database, tenant)).encf == Encf(T31, 7)
    with pytest.raises(NoNumberAvailableError):
        await allocated(module_database, tenant)
    with pytest.raises(NoNumberAvailableError):  # another company's ranges are not this one's
        await allocated(module_database, a_tenant())


class AbandonedError(Exception):
    """What a test raises to roll its unit back."""


async def allocated_then_abandoned(module_database: BoundDatabase, tenant: Tenant) -> None:
    """A unit that allocates the series' first number, then fails."""
    async with module_database.unit(tenant) as unit:
        assert (await bind(unit).ranges.allocate(TESTECF, T31, now=NOW)).encf == Encf(T31, 1)
        raise AbandonedError


async def test_a_unit_that_allocated_holds_the_series_until_it_ends(module_database: BoundDatabase) -> None:
    """The series' row is locked by the first allocation of a unit: another waits, and here gives up."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    holding, waiting = asyncio.Event(), asyncio.Event()

    async def first() -> Allocation:
        async with module_database.unit(tenant) as unit:
            taken = await bind(unit).ranges.allocate(TESTECF, T31, now=NOW)
            holding.set()
            await waiting.wait()
            return taken

    async def second() -> None:
        await holding.wait()
        try:
            async with module_database.unit(tenant) as unit:
                await unit.connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                await bind(unit).ranges.allocate(TESTECF, T31, now=NOW)
        finally:
            waiting.set()

    held, refused = await asyncio.gather(first(), second(), return_exceptions=True)
    assert isinstance(held, Allocation)
    assert held.encf == Encf(T31, 1)
    assert isinstance(refused, OperationalError)
    assert isinstance(refused.orig, LockNotAvailable)
    assert (await allocated(module_database, tenant)).encf == Encf(T31, 2)


async def test_a_number_allocated_in_a_unit_that_rolls_back_is_given_again(
    module_database: BoundDatabase,
) -> None:
    """Allocation commits with the unit that numbers: a unit that fails numbers nothing (Task 18 commits the
    numbered document before it signs or archives anything)."""
    tenant = a_tenant()
    await with_range(module_database, tenant)
    with pytest.raises(AbandonedError):
        await allocated_then_abandoned(module_database, tenant)
    assert (await allocated(module_database, tenant)).encf == Encf(T31, 1)
