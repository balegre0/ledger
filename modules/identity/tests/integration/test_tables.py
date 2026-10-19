"""identity's tables as migrated: row-level security hides what the scope does not own, and a row can never
point at another tenant's company, even where row-level security would let the row through."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, final

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from ledger_identity.adapters.postgres.tables import (
    ApiClientRecord,
    CompanyRecord,
    CompanyTaxIdRecord,
    EstablishmentRecord,
    IdentityTable,
    MembershipRecord,
    OrganizationRecord,
    TaxRegistrationRecord,
)
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 12, 12, tzinfo=UTC)
SUBJECT: Final = "312345678901234567"
INTRUDER: Final = "412345678901234567"
"""Every row a test slips across tenants is otherwise unique: only its foreign key can refuse it."""


@final
@dataclass(frozen=True, slots=True)
class Seeded:
    """A company with an establishment: what a test points at."""

    tenant: Tenant


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


def api_client_of(organization: OrganizationId, mode: str = "test") -> dict[str, object]:
    return {
        "id": new_id(),
        "organization_id": organization,
        "mode": mode,
        "subject": str(new_id()),
        "name": "Connector",
        "api_version": NOW.date(),
        "status": "active",
        "created_at": NOW,
        "created_by": SUBJECT,
        "revoked_at": None,
    }


def establishment_of(tenant: Tenant, code: str = "MAIN") -> dict[str, object]:
    return {
        "id": new_id(),
        "organization_id": tenant.organization,
        "company_id": tenant.company,
        "code": code,
        "name": "Main",
        "time_zone": "America/Santo_Domingo",
        "created_at": NOW,
    }


async def seeded(module_database: BoundDatabase, organization: OrganizationId | None = None) -> Seeded:
    """A company (of a new organization unless ``organization`` is given) with an establishment, an owner."""
    tenant = Tenant(
        organization=organization or OrganizationId(new_id()), company=CompanyId(new_id()), mode=None
    )
    async with module_database.unit(OrganizationScope(tenant.organization, None)) as unit:
        if organization is None:
            await unit.connection.execute(
                insert(OrganizationRecord).values(
                    id=tenant.organization,
                    provider_id="312345678901234567",
                    created_at=NOW,
                    api_version=NOW.date(),
                )
            )
        tax_value = str(tenant.company.int)[-9:]  # the random end of a UUIDv7
        await unit.connection.execute(
            insert(CompanyRecord).values(
                id=tenant.company,
                organization_id=tenant.organization,
                country="DO",
                subdivision=None,
                currency="DOP",
                legal_name="Acme SRL",
                status="active",
                fiscal_profile_locked_at=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await unit.connection.execute(
            insert(CompanyTaxIdRecord).values(
                organization_id=tenant.organization,
                company_id=tenant.company,
                position=0,
                scheme="do.rnc",
                value=tax_value,
            )
        )
        await unit.connection.execute(
            insert(TaxRegistrationRecord).values(
                scheme="do.rnc",
                value=tax_value,
                organization_id=tenant.organization,
                company_id=tenant.company,
            )
        )
        await unit.connection.execute(insert(MembershipRecord).values(membership_of(tenant)))
    async with module_database.unit(tenant) as unit:
        await unit.connection.execute(insert(EstablishmentRecord).values(establishment_of(tenant)))
    async with module_database.unit(OrganizationScope(tenant.organization, Mode.TEST)) as unit:
        await unit.connection.execute(insert(ApiClientRecord).values(api_client_of(tenant.organization)))
    return Seeded(tenant=tenant)


async def counts(module_database: BoundDatabase, scope: Scope | None) -> dict[str, int]:
    """How many rows of each of identity's tables ``scope`` sees."""
    async with module_database.unit(scope) as unit:
        return {
            table.name: (await unit.connection.execute(select(func.count()).select_from(table))).scalar_one()
            for table in IdentityTable.metadata.tables.values()
        }


def violated_key(caught: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The foreign key PostgreSQL refused the row by; ``None`` when something else refused it."""
    refusal = caught.value.orig
    return refusal.diag.constraint_name if isinstance(refusal, ForeignKeyViolation) else None


async def test_row_level_security_shows_a_scope_what_it_owns_and_nothing_without_one(
    module_database: BoundDatabase,
) -> None:
    first = await seeded(module_database)
    sibling = await seeded(
        module_database, first.tenant.organization
    )  # another company of the same organization
    other = await seeded(module_database)
    everything = {
        "organizations": 1,
        "companies": 2,
        "company_tax_ids": 2,
        "memberships": 2,
        "establishments": 2,
        "api_clients": 2,  # the organization's, whatever their mode (revision 0003)
    }
    registry = {
        "tax_registrations": 3
    }  # no tenant's: every scope sees every organization's tax ids, even none
    assert await counts(module_database, first.tenant) == everything | registry
    assert await counts(module_database, sibling.tenant) == everything | registry
    assert await counts(module_database, OrganizationScope(first.tenant.organization, None)) == {
        **everything,
        **registry,
    }
    assert await counts(module_database, other.tenant) == {
        **everything,
        **registry,
        "companies": 1,
        "company_tax_ids": 1,
        "memberships": 1,
        "establishments": 1,
        "api_clients": 1,
    }
    assert await counts(module_database, None) == dict.fromkeys(everything, 0) | registry


async def test_api_clients_show_a_unit_of_their_organization_whatever_its_mode_and_nobody_else(
    module_database: BoundDatabase,
) -> None:
    mine = await seeded(module_database)
    other = await seeded(module_database)
    organization = mine.tenant.organization
    async with module_database.unit(OrganizationScope(organization, Mode.LIVE)) as unit:
        await unit.connection.execute(insert(ApiClientRecord).values(api_client_of(organization, "live")))
    seen = {
        (scope.organization, scope.mode): (await counts(module_database, scope))["api_clients"]
        for scope in (
            OrganizationScope(organization, Mode.TEST),
            OrganizationScope(organization, Mode.LIVE),
            OrganizationScope(other.tenant.organization, Mode.TEST),
            OrganizationScope(other.tenant.organization, Mode.LIVE),
            OrganizationScope(organization, None),
            mine.tenant,  # a company's unit has no mode here either
        )
    }
    assert seen == {
        (organization, Mode.TEST): 2,  # the client a test unit made and the live one: the mode is a column
        (organization, Mode.LIVE): 2,
        (other.tenant.organization, Mode.TEST): 1,
        (other.tenant.organization, Mode.LIVE): 1,
        (organization, None): 2,
    }


async def test_an_api_client_of_another_organization_cannot_be_written_by_a_unit(
    module_database: BoundDatabase,
) -> None:
    mine = await seeded(module_database)
    other = await seeded(module_database)
    organization = mine.tenant.organization
    for scope, row in (
        (OrganizationScope(organization, Mode.TEST), api_client_of(other.tenant.organization)),
        (OrganizationScope(organization, None), api_client_of(other.tenant.organization, "live")),
        (None, api_client_of(organization)),
    ):
        with pytest.raises(ProgrammingError) as caught:
            async with module_database.unit(scope) as unit:
                await unit.connection.execute(insert(ApiClientRecord).values(row))
        assert isinstance(caught.value.orig, InsufficientPrivilege)  # the policy refuses the row


async def test_a_membership_cannot_point_at_another_organizations_company(
    module_database: BoundDatabase,
) -> None:
    victim = await seeded(module_database)
    attacker = await seeded(module_database)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(
            OrganizationScope(attacker.tenant.organization, None)
        ) as unit:  # the policy lets the row in
            await unit.connection.execute(insert(MembershipRecord).values(membership_of(across, INTRUDER)))
    assert violated_key(caught) == "fk_memberships_company"


async def test_an_establishment_cannot_point_at_another_organizations_company(
    module_database: BoundDatabase,
) -> None:
    victim = await seeded(module_database)
    attacker = await seeded(module_database)
    across = Tenant(organization=attacker.tenant.organization, company=victim.tenant.company, mode=None)
    with pytest.raises(IntegrityError) as caught:
        async with module_database.unit(across) as unit:
            await unit.connection.execute(insert(EstablishmentRecord).values(establishment_of(across, "999")))
    assert violated_key(caught) == "fk_establishments_company"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM platform.jobs",
        "SELECT version FROM platform.routing",
        "DELETE FROM platform.idempotency_keys",
    ],
)
async def test_identity_s_role_reaches_no_other_schema(
    module_database: BoundDatabase, statement: str
) -> None:
    """A statement naming another schema's table fails, even in a valid unit (Review Focus 5)."""
    with pytest.raises(ProgrammingError) as caught:
        async with module_database.unit(OrganizationScope(OrganizationId(new_id()), None)) as unit:
            await unit.connection.execute(text(statement))
    assert isinstance(caught.value.orig, InsufficientPrivilege)
