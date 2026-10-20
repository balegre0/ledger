"""fiscal's tables as migrated: a company's unit in a mode sees its own enrollments, and nothing else.

Row-level security denies by default: a unit without a scope, an organization's unit, another company's or
the other mode's sees none, and none can write a row it does not own. The repository keeps what it is
given and reads it back the same, and refuses an enrollment of another tenant before any statement.
"""

from datetime import UTC, datetime
from typing import Final

import pytest
from psycopg.errors import CheckViolation, InsufficientPrivilege, UniqueViolation
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from ledger_fiscal.adapters.postgres.repositories.enrollments import ForeignTenantError, PgEnrollments
from ledger_fiscal.adapters.postgres.tables import EnrollmentRecord
from ledger_fiscal.adapters.postgres.unit import bind
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.pagination import PageRequest
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)


class Due:
    """Requirements of nothing at all, but a range."""

    currently_due: tuple[str, ...] = ("do_ecf.range",)
    disabled_reason: str | None = None


def a_tenant(organization: OrganizationId | None = None, mode: Mode = Mode.TEST) -> Tenant:
    return Tenant(organization or OrganizationId(new_id()), CompanyId(new_id()), mode)


def new(tenant: Tenant, channel: str = "do-fake-v1", environment: str = "testecf") -> Enrollment:
    asked = Asked(enrollment=EnrollmentId(new_id()), tenant=tenant, channel=channel, environment=environment)
    return Enrollment.enroll(asked, requirements=Due(), now=NOW)


async def kept(module_database: BoundDatabase, enrollment: Enrollment) -> EnrollmentId | None:
    async with module_database.unit(enrollment.tenant) as unit:
        return await bind(unit).enrollments.add(enrollment)


async def count(module_database: BoundDatabase, scope: Scope | None) -> int:
    async with module_database.unit(scope) as unit:
        found = await unit.connection.execute(select(func.count()).select_from(EnrollmentRecord))
        return found.scalar_one()


async def test_a_tenant_s_unit_sees_its_own_enrollments_and_nothing_else(
    module_database: BoundDatabase,
) -> None:
    mine = a_tenant()
    await kept(module_database, new(mine))
    await kept(module_database, new(a_tenant(mine.organization)))  # a sibling company's
    assert await count(module_database, mine) == 1
    assert await count(module_database, Tenant(mine.organization, mine.company, Mode.LIVE)) == 0
    assert await count(module_database, Tenant(mine.organization, mine.company, None)) == 0
    assert await count(module_database, OrganizationScope(mine.organization, Mode.TEST)) == 0
    assert await count(module_database, a_tenant()) == 0
    assert await count(module_database, None) == 0


@pytest.mark.parametrize("scope", [None, "another company", "the other mode"])
async def test_a_unit_cannot_write_an_enrollment_of_another_tenant(
    module_database: BoundDatabase, scope: str | None
) -> None:
    victim = new(a_tenant())
    units: dict[str | None, Scope | None] = {
        None: None,
        "another company": a_tenant(),
        "the other mode": Tenant(victim.organization, victim.company, Mode.LIVE),
    }
    row: dict[str, object] = {
        "organization_id": victim.organization,
        "company_id": victim.company,
        "mode": "test",
        "id": victim.id,
        "channel": victim.channel,
        "environment": victim.environment,
        "status": "pending",
        "currently_due": [],
        "disabled_reason": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    with pytest.raises(ProgrammingError) as caught:
        async with module_database.unit(units[scope]) as unit:
            await unit.connection.execute(insert(EnrollmentRecord).values(row))
    assert isinstance(caught.value.orig, InsufficientPrivilege)


async def test_a_second_enrollment_on_a_channel_is_answered_with_its_holder(
    module_database: BoundDatabase,
) -> None:
    tenant = a_tenant()
    first = new(tenant)
    assert await kept(module_database, first) is None
    assert await kept(module_database, new(tenant, environment="certecf")) == first.id
    assert await kept(module_database, new(tenant, channel="do-other-v1")) is None
    live = Tenant(tenant.organization, tenant.company, Mode.LIVE)
    assert await kept(module_database, new(live, environment="ecf")) is None


async def test_the_index_and_checks_hold_whatever_the_repository_does(module_database: BoundDatabase) -> None:
    tenant = a_tenant()
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(EnrollmentRecord).values(_row(tenant)))
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(insert(EnrollmentRecord).values(_row(tenant)))
    assert isinstance(caught.value.orig, UniqueViolation)
    with pytest.raises(IntegrityError) as refused:
        async with module_database.unit(tenant) as unit:
            await unit.connection.execute(insert(EnrollmentRecord).values(_row(tenant, status="disabled")))
    assert isinstance(refused.value.orig, CheckViolation)


def _row(tenant: Tenant, *, status: str = "pending") -> dict[str, object]:
    return {
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "mode": "test",
        "id": new_id(),
        "channel": "do-fake-v1",
        "environment": "testecf",
        "status": status,
        "currently_due": [],
        "disabled_reason": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


async def test_an_enrollment_reads_back_as_kept_and_changes_in_place(module_database: BoundDatabase) -> None:
    tenant = a_tenant()
    enrollment = new(tenant)
    await kept(module_database, enrollment)
    disabled = enrollment.disabled("Gone", now=NOW)
    async with module_database.unit(tenant) as unit:
        enrollments = bind(unit).enrollments
        assert await enrollments.get(enrollment.id) == enrollment
        assert await enrollments.on("do-fake-v1", "testecf") == enrollment
        assert await enrollments.locked(enrollment.id) == enrollment
        await enrollments.update(disabled, version=enrollment.version)
    async with module_database.unit(tenant) as unit:
        enrollments = bind(unit).enrollments
        assert await enrollments.get(enrollment.id) == disabled
        assert await enrollments.on("do-fake-v1", "testecf") is None
        assert await enrollments.every() == (disabled,)
        assert (await enrollments.page(PageRequest())).items == (disabled,)
        assert await enrollments.get(EnrollmentId(new_id())) is None


async def test_an_enrollment_of_another_tenant_is_refused_before_any_statement(
    module_database: BoundDatabase,
) -> None:
    tenant = a_tenant()
    async with module_database.unit(tenant) as unit:
        enrollments = PgEnrollments(unit.connection, tenant, Mode.TEST)
        stranger = new(a_tenant())
        with pytest.raises(ForeignTenantError):
            await enrollments.add(stranger)
        with pytest.raises(ForeignTenantError):
            await enrollments.update(stranger, version=1)


async def test_a_unit_without_a_company_in_a_mode_has_no_fiscal_view(module_database: BoundDatabase) -> None:
    tenant = a_tenant()
    for scope in (None, Tenant(tenant.organization, tenant.company, None)):
        async with module_database.unit(scope) as unit:
            with pytest.raises(InvalidValueError, match=r"unit\.scope"):
                bind(unit)
