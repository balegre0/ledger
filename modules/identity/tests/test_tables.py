"""identity's tables as migrated: row-level security hides what the scope does not own, and a row can never
point at another tenant's company or branch, even where row-level security would let the row through."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, final
lazy from uuid import UUID

import pytest
from psycopg.errors import ForeignKeyViolation
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from ledger_identity.adapters.postgres.tables import (
    BranchRecord,
    CompanyRecord,
    EmissionPointRecord,
    IdentityTable,
    MembershipRecord,
    OrganizationRecord,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.database import Database

NOW: Final = datetime(2026, 10, 12, 12, tzinfo=UTC)
SUBJECT: Final = "312345678901234567"
INTRUDER: Final = "412345678901234567"
"""Every row a test slips across tenants is otherwise unique: only its foreign key can refuse it."""


@final
@dataclass(frozen=True, slots=True)
class Seeded:
    """A company with a branch: the ids a test points at."""

    tenant: Tenant
    branch: UUID


def membership_of(tenant: Tenant, subject: str = SUBJECT) -> dict[str, object]:
    return {
        "id": new_id(),
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "subject": subject,
        "role": "owner",
        "status": "active",
        "version": 1,
        "changed_at": NOW,
        "changed_by": SUBJECT,
    }


def branch_of(tenant: Tenant, branch: UUID, code: str = "001") -> dict[str, object]:
    return {
        "id": branch,
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "code": code,
        "name": "Main",
        "created_at": NOW,
    }


def point_of(tenant: Tenant, branch: UUID, code: str = "001") -> dict[str, object]:
    return {
        "id": new_id(),
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "branch_id": branch,
        "code": code,
        "name": "Counter",
        "created_at": NOW,
    }


async def seeded(database: Database, organization: OrganizationId | None = None) -> Seeded:
    """A company (of a new organization unless ``organization`` is given) with one branch and one owner."""
    tenant = Tenant(
        organization=organization or OrganizationId(new_id()), company=CompanyId(new_id()), mode=None
    )
    async with database.transaction(OrganizationScope(tenant.organization, None)) as unit:
        if organization is None:
            await unit.connection.execute(
                insert(OrganizationRecord).values(
                    id=tenant.organization, provider_id="312345678901234567", created_at=NOW
                )
            )
        await unit.connection.execute(
            insert(CompanyRecord).values(
                id=tenant.company,
                organization_id=tenant.organization,
                tax_id=str(tenant.company.int)[-9:],  # the random end of a UUIDv7
                legal_name="Acme SRL",
                status="active",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await unit.connection.execute(insert(MembershipRecord).values(membership_of(tenant)))
    branch = new_id()
    async with database.transaction(tenant) as unit:
        await unit.connection.execute(insert(BranchRecord).values(branch_of(tenant, branch)))
        await unit.connection.execute(insert(EmissionPointRecord).values(point_of(tenant, branch)))
    return Seeded(tenant=tenant, branch=branch)


async def counts(database: Database, scope: Scope | None) -> dict[str, int]:
    """How many rows of each of identity's tables ``scope`` sees."""
    async with database.transaction(scope) as unit:
        return {
            name: (await unit.connection.execute(select(func.count()).select_from(table))).scalar_one()
            for name, table in IdentityTable.metadata.tables.items()
        }


def violated_key(caught: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The foreign key PostgreSQL refused the row by; ``None`` when something else refused it."""
    refusal = caught.value.orig
    return refusal.diag.constraint_name if isinstance(refusal, ForeignKeyViolation) else None


async def test_row_level_security_shows_a_scope_what_it_owns_and_nothing_without_one(
    database: Database,
) -> None:
    first = await seeded(database)
    sibling = await seeded(database, first.tenant.organization)  # another company of the same organization
    other = await seeded(database)
    everything = {"organizations": 1, "companies": 2, "memberships": 2, "branches": 1, "emission_points": 1}
    assert await counts(database, first.tenant) == everything
    assert await counts(database, sibling.tenant) == everything
    assert await counts(database, OrganizationScope(first.tenant.organization, None)) == {
        **everything,
        "branches": 0,
        "emission_points": 0,
    }
    assert await counts(database, other.tenant) == {**everything, "companies": 1, "memberships": 1}
    assert await counts(database, None) == dict.fromkeys(IdentityTable.metadata.tables, 0)


async def test_a_membership_cannot_point_at_another_organizations_company(database: Database) -> None:
    victim = await seeded(database)
    attacker = await seeded(database)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with database.transaction(
            OrganizationScope(attacker.tenant.organization, None)
        ) as unit:  # the policy lets the row in
            await unit.connection.execute(insert(MembershipRecord).values(membership_of(across, INTRUDER)))
    assert violated_key(caught) == "fk_memberships_company"


async def test_a_branch_cannot_point_at_another_organizations_company(database: Database) -> None:
    victim = await seeded(database)
    attacker = await seeded(database)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with database.transaction(across) as unit:
            await unit.connection.execute(insert(BranchRecord).values(branch_of(across, new_id(), "999")))
    assert violated_key(caught) == "fk_branches_company"


async def test_an_emission_point_cannot_point_at_the_branch_of_another_company(database: Database) -> None:
    victim = await seeded(database)
    attacker = await seeded(database, victim.tenant.organization)  # the same organization, another company
    with pytest.raises(IntegrityError) as caught:
        async with database.transaction(attacker.tenant) as unit:
            await unit.connection.execute(
                insert(EmissionPointRecord).values(point_of(attacker.tenant, victim.branch, "999"))
            )
    assert violated_key(caught) == "fk_emission_points_branch"


async def test_an_emission_point_cannot_point_at_another_organizations_company(database: Database) -> None:
    victim = await seeded(database)
    attacker = await seeded(database)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with database.transaction(across) as unit:
            await unit.connection.execute(
                insert(EmissionPointRecord).values(point_of(across, victim.branch, "999"))
            )
    assert violated_key(caught) == "fk_emission_points_company"
