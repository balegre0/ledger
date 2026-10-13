"""identity's tables as migrated: row-level security hides what the scope does not own, and a row can never
point at another tenant's company or branch, even where row-level security would let the row through."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, final
lazy from uuid import UUID

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

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
lazy from ledger_platform.data.bound import BoundDatabase

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


async def seeded(identity_db: BoundDatabase, organization: OrganizationId | None = None) -> Seeded:
    """A company (of a new organization unless ``organization`` is given) with one branch and one owner."""
    tenant = Tenant(
        organization=organization or OrganizationId(new_id()), company=CompanyId(new_id()), mode=None
    )
    async with identity_db.unit(OrganizationScope(tenant.organization, None)) as unit:
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
    async with identity_db.unit(tenant) as unit:
        await unit.connection.execute(insert(BranchRecord).values(branch_of(tenant, branch)))
        await unit.connection.execute(insert(EmissionPointRecord).values(point_of(tenant, branch)))
    return Seeded(tenant=tenant, branch=branch)


async def counts(identity_db: BoundDatabase, scope: Scope | None) -> dict[str, int]:
    """How many rows of each of identity's tables ``scope`` sees."""
    async with identity_db.unit(scope) as unit:
        return {
            table.name: (await unit.connection.execute(select(func.count()).select_from(table))).scalar_one()
            for table in IdentityTable.metadata.tables.values()
        }


def violated_key(caught: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The foreign key PostgreSQL refused the row by; ``None`` when something else refused it."""
    refusal = caught.value.orig
    return refusal.diag.constraint_name if isinstance(refusal, ForeignKeyViolation) else None


async def test_row_level_security_shows_a_scope_what_it_owns_and_nothing_without_one(
    identity_db: BoundDatabase,
) -> None:
    first = await seeded(identity_db)
    sibling = await seeded(identity_db, first.tenant.organization)  # another company of the same organization
    other = await seeded(identity_db)
    everything = {"organizations": 1, "companies": 2, "memberships": 2, "branches": 1, "emission_points": 1}
    assert await counts(identity_db, first.tenant) == everything
    assert await counts(identity_db, sibling.tenant) == everything
    assert await counts(identity_db, OrganizationScope(first.tenant.organization, None)) == {
        **everything,
        "branches": 0,
        "emission_points": 0,
    }
    assert await counts(identity_db, other.tenant) == {**everything, "companies": 1, "memberships": 1}
    assert await counts(identity_db, None) == dict.fromkeys(everything, 0)


async def test_a_membership_cannot_point_at_another_organizations_company(identity_db: BoundDatabase) -> None:
    victim = await seeded(identity_db)
    attacker = await seeded(identity_db)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with identity_db.unit(
            OrganizationScope(attacker.tenant.organization, None)
        ) as unit:  # the policy lets the row in
            await unit.connection.execute(insert(MembershipRecord).values(membership_of(across, INTRUDER)))
    assert violated_key(caught) == "fk_memberships_company"


async def test_a_branch_cannot_point_at_another_organizations_company(identity_db: BoundDatabase) -> None:
    victim = await seeded(identity_db)
    attacker = await seeded(identity_db)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with identity_db.unit(across) as unit:
            await unit.connection.execute(insert(BranchRecord).values(branch_of(across, new_id(), "999")))
    assert violated_key(caught) == "fk_branches_company"


async def test_an_emission_point_cannot_point_at_the_branch_of_another_company(
    identity_db: BoundDatabase,
) -> None:
    victim = await seeded(identity_db)
    attacker = await seeded(identity_db, victim.tenant.organization)  # the same organization, another company
    with pytest.raises(IntegrityError) as caught:
        async with identity_db.unit(attacker.tenant) as unit:
            await unit.connection.execute(
                insert(EmissionPointRecord).values(point_of(attacker.tenant, victim.branch, "999"))
            )
    assert violated_key(caught) == "fk_emission_points_branch"


async def test_an_emission_point_cannot_point_at_another_organizations_company(
    identity_db: BoundDatabase,
) -> None:
    victim = await seeded(identity_db)
    attacker = await seeded(identity_db)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with identity_db.unit(across) as unit:
            await unit.connection.execute(
                insert(EmissionPointRecord).values(point_of(across, victim.branch, "999"))
            )
    assert violated_key(caught) == "fk_emission_points_company"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM platform.jobs",
        "SELECT version FROM platform.routing",
        "DELETE FROM platform.idempotency_keys",
    ],
)
async def test_identity_s_role_reaches_no_other_schema(identity_db: BoundDatabase, statement: str) -> None:
    """A statement naming another schema's table fails, even in a valid unit (Review Focus 5)."""
    with pytest.raises(ProgrammingError) as caught:
        async with identity_db.unit(OrganizationScope(OrganizationId(new_id()), None)) as unit:
            await unit.connection.execute(text(statement))
    assert isinstance(caught.value.orig, InsufficientPrivilege)
