"""identity's state in PostgreSQL: repositories, units, the outbox and the permission source, for real.

Units are identity's own (``identity_db``: ``identity_rw``, its schema); what they announced is read from
``identity.outbox`` past row-level security (``admin``).
"""

import asyncio
from datetime import UTC, datetime
from typing import Final, final
lazy from collections.abc import Awaitable, Callable

import psycopg
import pytest
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy import Insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from ledger_identity.adapters.permissions import MembershipPermissions
from ledger_identity.adapters.postgres.repositories import ForeignTenantError
from ledger_identity.adapters.postgres.tables import (
    BranchRecord,
    CompanyRecord,
    EmissionPointRecord,
    MembershipRecord,
)
from ledger_identity.adapters.postgres.unit import UnscopedUnitError, bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.branches import Branch, EmissionPoint
from ledger_identity.domain.errors import (
    BranchCodeTakenError,
    CompanyInactiveError,
    CompanyNotFoundError,
    EmissionPointCodeTakenError,
    OwnersOnlyError,
    TaxIdTakenError,
)
from ledger_identity.domain.membership import found
from ledger_identity.domain.organization import ORGANIZATION_ADMIN, Organization
from ledger_identity.domain.roles import READ_ONLY, Role
from ledger_identity.domain.text import parse_code, parse_legal_name, parse_name
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.pagination import PageRequest, decode_cursor
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.database import app_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.pagination import Page
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.data.notifier import Channel
lazy from ledger_testing.postgres import WorkerDatabase

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FOUNDER: Final = Principal(
    subject=SubjectId("2001"),
    organization=organization_of("1001"),
    provider_organization="1001",
    roles=frozenset({ORGANIZATION_ADMIN}),
)
PARTNER: Final = Principal(
    subject=SubjectId("2002"), organization=FOUNDER.organization, provider_organization="1001"
)
STRANGER: Final = Principal(
    subject=SubjectId("2001"),
    organization=organization_of("1002"),
    provider_organization="1002",
    roles=frozenset({ORGANIZATION_ADMIN}),
)
OWNER: Final = FOUNDER.subject
MEMBER: Final = Principal(
    subject=SubjectId("3001"), organization=FOUNDER.organization, provider_organization="1001"
)
RNC: Final = TaxId.parse("101850043")
THEIRS: Final = TaxId.parse("401500022")
"""The tax id of the other organization's company: one taxpayer is one active company."""
SHOWING: Final = text(
    """
    SELECT set_config('ledger.organization_id', :organization, true),
           set_config('ledger.company_id', :company, true)
    """
)
"""Row-level security made to show another tenant's rows, for the rest of a unit."""
TYPES: Final = TypeAdapter(list[tuple[str]])
MOMENT: Final = TypeAdapter(AwareDatetime)


@final
class RecordedSignals:
    """``Signals`` that write down what they were told, in order."""

    __slots__ = ("sent",)

    def __init__(self) -> None:
        self.sent: list[Channel] = []

    def signal(self, channel: Channel) -> None:
        self.sent.append(channel)


@pytest.fixture
def signals() -> RecordedSignals:
    """What identity's units signalled after their commits (``identity_db`` signals here)."""
    return RecordedSignals()


def anything(version: int) -> bool:
    del version
    return True


async def in_unit[T](
    identity_db: BoundDatabase, scope: Scope, work: Callable[[IdentityUnit], Awaitable[T]]
) -> T:
    async with identity_db.unit(scope) as unit:
        return await work(bind(unit))


async def create(identity_db: BoundDatabase, founder: Principal = FOUNDER, tax_id: TaxId = RNC) -> Company:
    return await in_unit(
        identity_db,
        OrganizationScope(founder.organization, None),
        lambda unit: commands.create_company(
            unit, founder, tax_id=tax_id, legal_name=parse_legal_name("Duarte")
        ),
    )


async def write(identity_db: BoundDatabase, scope: Scope, statement: Insert) -> None:
    async with identity_db.unit(scope) as unit:
        await unit.connection.execute(statement)


async def published(admin: psycopg.AsyncConnection) -> list[str]:
    """The types of the events identity's outbox holds, oldest first."""
    cursor = await admin.execute("SELECT type FROM identity.outbox ORDER BY id")
    return [kind for (kind,) in TYPES.validate_python(await cursor.fetchall())]


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_company_is_kept_with_its_owner_and_organization(identity_db: BoundDatabase) -> None:
    company = await create(identity_db)
    tenant = company.tenant
    read = await in_unit(identity_db, tenant, lambda unit: queries.company(unit, company.id))
    assert read == company
    organization = await in_unit(
        identity_db, tenant, lambda unit: queries.organization(unit, tenant.organization)
    )
    assert (organization.id, organization.provider_id) == (FOUNDER.organization, "1001")
    permissions = await in_unit(
        identity_db, tenant, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert permissions == frozenset(Permission)


async def test_what_a_unit_changes_is_announced_through_the_outbox_when_it_commits(
    identity_db: BoundDatabase, signals: RecordedSignals, admin: psycopg.AsyncConnection
) -> None:
    company = await create(identity_db)
    cursor = await admin.execute(
        """
        SELECT source, organization_id, company_id, mode, subject, aggregate_version, occurred_at
        FROM identity.outbox ORDER BY id
        """
    )
    registered, granted = await cursor.fetchall()
    assert tuple(registered) == (
        "identity",
        company.organization,
        company.id,
        None,
        company.id,
        1,
        company.created_at,
    )
    assert tuple(granted)[:4] == ("identity", company.organization, company.id, None)
    assert await published(admin) == ["identity.company.registered", "identity.membership.granted"]
    assert signals.sent == ["ledger_outbox"]  # once, after the commit


async def test_a_unit_that_fails_keeps_and_announces_nothing(
    identity_db: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async def create_then_fail(unit: IdentityUnit) -> None:
        await commands.create_company(unit, FOUNDER, tax_id=RNC, legal_name=parse_legal_name("Duarte"))
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await in_unit(identity_db, OrganizationScope(FOUNDER.organization, None), create_then_fail)
    async with identity_db.unit(
        OrganizationScope(FOUNDER.organization, None)
    ) as unit:  # the scope that would see the company
        companies = (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one()
    assert (companies, await published(admin)) == (0, [])


async def test_a_tax_id_is_one_active_company_s_in_every_organization(identity_db: BoundDatabase) -> None:
    """One RNC is one taxpayer: another organization cannot register it, though it sees none of the first's
    rows (the unique index enforces it whatever the policies show)."""
    await create(identity_db)
    with pytest.raises(TaxIdTakenError):
        await create(identity_db)
    with pytest.raises(TaxIdTakenError):
        await create(identity_db, founder=STRANGER)
    async with identity_db.unit(OrganizationScope(STRANGER.organization, None)) as unit:
        assert (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one() == 0


async def test_two_organizations_registering_one_taxpayer_at_once_get_one_company(
    identity_db: BoundDatabase,
) -> None:
    outcomes = await asyncio.gather(
        create(identity_db), create(identity_db, founder=STRANGER), return_exceptions=True
    )
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ["Company", "TaxIdTakenError"]


async def deactivate(identity_db: BoundDatabase, company: Company) -> None:
    tenant = Tenant(company.organization, company.id, None)
    await in_unit(
        identity_db,
        tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=lambda _: True, actor=OWNER),
    )


async def test_a_deactivated_company_frees_its_tax_id_for_any_organization(
    identity_db: BoundDatabase,
) -> None:
    first = await create(identity_db)
    await deactivate(identity_db, first)
    again = await create(identity_db)  # the same organization registers the taxpayer again
    await deactivate(identity_db, again)
    theirs = await create(identity_db, founder=STRANGER)  # or another one does
    assert len({first.id, again.id, theirs.id}) == 3
    with pytest.raises(TaxIdTakenError):
        await create(identity_db)


async def test_another_organization_sees_none_of_a_company(identity_db: BoundDatabase) -> None:
    company = await create(identity_db)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(
            identity_db,
            OrganizationScope(STRANGER.organization, None),
            lambda unit: queries.company(unit, company.id),
        )
    stranger_tenant = Tenant(STRANGER.organization, company.id, None)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(identity_db, stranger_tenant, lambda unit: queries.company(unit, company.id))
    assert await MembershipPermissions(identity_db).permissions(STRANGER, company.id) == frozenset()


async def test_a_change_applies_to_the_version_it_was_made_from_only(identity_db: BoundDatabase) -> None:
    company = await create(identity_db)
    renamed, _ = company.rename(parse_legal_name("Duarte Hermanos"), now=NOW)
    assert await in_unit(identity_db, company.tenant, lambda unit: unit.companies.update(renamed, version=1))
    again, _ = company.rename(parse_legal_name("Duarte e Hijos"), now=NOW)
    assert not await in_unit(
        identity_db, company.tenant, lambda unit: unit.companies.update(again, version=1)
    )
    assert (
        await in_unit(identity_db, company.tenant, lambda unit: queries.company(unit, company.id))
    ).legal_name == ("Duarte Hermanos")


async def test_renames_and_deactivations_are_announced(
    identity_db: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(identity_db)
    await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.rename_company(
            unit, company.id, legal_name=parse_legal_name("Duarte S.A."), admits=anything, actor=OWNER
        ),
    )
    await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    assert await published(admin) == [
        "identity.company.registered",
        "identity.membership.granted",
        "identity.company.updated",
        "identity.company.deactivated",
    ]


async def test_a_principal_pages_through_the_companies_it_holds_a_role_in(identity_db: BoundDatabase) -> None:
    for rnc in ("101850043", "130000012", "401007551"):
        await create(identity_db, tax_id=TaxId.parse(rnc))
    await create(identity_db, founder=STRANGER, tax_id=THEIRS)
    first = await in_unit(
        identity_db,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2)),
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None
    after = first.items[-1].id
    rest = await in_unit(
        identity_db,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2, after=after)),
    )
    assert (len(rest.items), rest.next_cursor) == (1, None)
    assert all(company.organization == FOUNDER.organization for company in (*first.items, *rest.items))
    partner = await in_unit(
        identity_db,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, PARTNER.subject, PageRequest()),
    )
    assert partner.items == ()


# ── Branches and emission points ───────────────────────────────────────────────


async def test_branches_and_emission_points_are_kept_per_company(identity_db: BoundDatabase) -> None:
    company = await create(identity_db)
    other = await create(identity_db, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, commands.Opening(parse_code("SUC1"), parse_name("Santiago")), actor=OWNER
        ),
    )
    await in_unit(
        identity_db,
        other.tenant,
        lambda unit: commands.open_branch(
            unit, other.id, commands.Opening(parse_code("SUC1"), parse_name("Bonao")), actor=OWNER
        ),
    )
    with pytest.raises(BranchCodeTakenError):
        await in_unit(
            identity_db,
            company.tenant,
            lambda unit: commands.open_branch(
                unit, company.id, commands.Opening(parse_code("SUC1"), parse_name("X")), actor=OWNER
            ),
        )
    point = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, commands.Opening(parse_code("001"), parse_name("Caja")), actor=OWNER
        ),
    )
    with pytest.raises(EmissionPointCodeTakenError):
        await in_unit(
            identity_db,
            company.tenant,
            lambda unit: commands.open_emission_point(
                unit, company.id, branch.id, commands.Opening(parse_code("001"), parse_name("Y")), actor=OWNER
            ),
        )
    assert (
        await in_unit(identity_db, company.tenant, lambda unit: queries.branch(unit, company.id, branch.id))
        == branch
    )
    assert (
        await in_unit(
            identity_db,
            company.tenant,
            lambda unit: queries.emission_point(unit, company.id, branch.id, point.id),
        )
        == point
    )
    branches = await in_unit(
        identity_db, company.tenant, lambda unit: queries.branches(unit, company.id, PageRequest())
    )
    points = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: queries.emission_points(unit, company.id, branch.id, PageRequest()),
    )
    assert (branches.items, points.items) == ((branch,), (point,))


async def test_a_company_scope_sees_no_branch_of_another_company(identity_db: BoundDatabase) -> None:
    company = await create(identity_db)
    other = await create(identity_db, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, commands.Opening(parse_code("SUC1"), parse_name("Santiago")), actor=OWNER
        ),
    )
    seen_by_other = await in_unit(
        identity_db, other.tenant, lambda unit: unit.branches.page(company.id, PageRequest())
    )
    asked_wrongly = await in_unit(
        identity_db, company.tenant, lambda unit: unit.branches.get(other.id, branch.id)
    )
    assert (seen_by_other.items, asked_wrongly) == ((), None)


# ── Tenancy checked twice ───────────────────────────────────────────────────────


async def test_a_row_cannot_point_at_a_company_or_a_branch_of_another_tenant(
    identity_db: BoundDatabase,
) -> None:
    mine = await create(identity_db)
    sister = await create(identity_db, tax_id=TaxId.parse("130000012"))
    theirs = await create(identity_db, founder=STRANGER, tax_id=THEIRS)
    branch = await in_unit(
        identity_db,
        sister.tenant,
        lambda unit: commands.open_branch(
            unit, sister.id, commands.Opening(parse_code("SUC1"), parse_name("Bonao")), actor=OWNER
        ),
    )
    member_of_theirs = insert(MembershipRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=theirs.id,
        subject="3001",
        role="viewer",
        status="active",
        version=1,
        changed_at=NOW,
        changed_by="2001",
    )
    branch_of_theirs = insert(BranchRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=theirs.id,
        code="SUC9",
        name="X",
        created_at=NOW,
    )
    point_in_the_sister_s_branch = insert(EmissionPointRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=mine.id,
        branch_id=branch.id,
        code="001",
        name="Caja",
        created_at=NOW,
    )
    attempts: tuple[tuple[Scope, Insert], ...] = (
        (
            OrganizationScope(mine.organization, None),
            member_of_theirs,
        ),  # row-level security lets each of these through
        (Tenant(mine.organization, theirs.id, None), branch_of_theirs),
        (mine.tenant, point_in_the_sister_s_branch),
    )
    for scope, statement in attempts:
        with pytest.raises(IntegrityError) as caught:
            await write(identity_db, scope, statement)
        assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


async def with_the_policies_showing[T](
    identity_db: BoundDatabase,
    scope: Scope,
    shown: Tenant,
    look: Callable[[IdentityUnit, AsyncConnection], Awaitable[T]],
) -> T:
    """What ``look`` sees in a unit of ``scope`` whose row-level security shows ``shown``'s rows instead.

    What a policy dropped by mistake (or a setting gone wrong) would leave: the repositories, bound to the
    unit's own scope, are the only filter between the unit and another tenant's rows.
    """
    async with identity_db.unit(scope) as unit:
        await unit.connection.execute(
            SHOWING, {"organization": str(shown.organization), "company": str(shown.company)}
        )
        return await look(bind(unit), unit.connection)


async def a_company_with_a_branch_and_a_point(
    identity_db: BoundDatabase,
) -> tuple[Company, Branch, EmissionPoint]:
    company = await create(identity_db)
    branch = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, commands.Opening(parse_code("SUC1"), parse_name("S")), actor=OWNER
        ),
    )
    point = await in_unit(
        identity_db,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, commands.Opening(parse_code("001"), parse_name("Caja")), actor=OWNER
        ),
    )
    return company, branch, point


async def test_the_repositories_keep_to_the_unit_s_organization_without_row_level_security(
    identity_db: BoundDatabase,
) -> None:
    company, branch, point = await a_company_with_a_branch_and_a_point(identity_db)

    async def look(unit: IdentityUnit, connection: AsyncConnection) -> list[object]:
        every_company = select(func.count()).select_from(CompanyRecord)
        return [
            (await connection.execute(every_company)).scalar_one(),
            await unit.organizations.get(FOUNDER.organization),
            await unit.companies.get(company.id),
            await unit.companies.locked(company.id),
            (await unit.companies.of_member(OWNER, PageRequest())).items,
            await unit.memberships.get(company.id, OWNER),
            await unit.memberships.owners(company.id),
            (await unit.memberships.page(company.id, PageRequest())).items,
            await unit.branches.get(company.id, branch.id),
            (await unit.branches.page(company.id, PageRequest())).items,
            await unit.emission_points.get(company.id, point.id),
            (await unit.emission_points.page(company.id, branch.id, PageRequest())).items,
        ]

    seen = await with_the_policies_showing(
        identity_db, OrganizationScope(STRANGER.organization, None), company.tenant, look
    )
    assert seen == [1, None, None, None, (), None, 0, (), None, (), None, ()]


async def test_the_repositories_keep_to_the_unit_s_company_without_row_level_security(
    identity_db: BoundDatabase,
) -> None:
    company, branch, point = await a_company_with_a_branch_and_a_point(identity_db)
    sister = await create(identity_db, tax_id=TaxId.parse("130000012"))

    async def look(unit: IdentityUnit, connection: AsyncConnection) -> list[object]:
        del connection
        return [
            await unit.branches.get(company.id, branch.id),
            (await unit.branches.page(company.id, PageRequest())).items,
            await unit.emission_points.get(company.id, point.id),
            (await unit.emission_points.page(company.id, branch.id, PageRequest())).items,
        ]

    nothing: Final = [None, (), None, ()]
    shown = company.tenant
    assert await with_the_policies_showing(identity_db, sister.tenant, shown, look) == nothing
    whole = OrganizationScope(company.organization, None)
    assert await with_the_policies_showing(identity_db, whole, shown, look) == nothing
    own = await with_the_policies_showing(identity_db, company.tenant, shown, look)
    assert own == [branch, (branch,), point, (point,)]  # the same statements, in the company's own unit


async def test_a_unit_refuses_to_write_a_row_of_another_tenant(identity_db: BoundDatabase) -> None:
    """Row-level security would refuse each of these too: the repositories do not count on it."""
    company = await create(identity_db)
    sister = await create(identity_db, tax_id=TaxId.parse("130000012"))
    branch = Branch.open(company, code=parse_code("SUC1"), name=parse_name("S"), now=NOW)
    point = EmissionPoint.open(company, branch, code=parse_code("001"), name=parse_name("Caja"), now=NOW)
    owner, _ = found(company, FOUNDER, now=NOW)
    renamed, _ = company.rename(parse_legal_name("Duarte S.A."), now=NOW)
    of_another_organization: tuple[Callable[[IdentityUnit], Awaitable[object]], ...] = (
        lambda unit: unit.organizations.add(Organization.of(FOUNDER, now=NOW)),
        lambda unit: unit.companies.add(company),
        lambda unit: unit.companies.update(renamed, version=company.version),
        lambda unit: unit.branches.add(branch),
        lambda unit: unit.emission_points.add(point),
        lambda unit: unit.memberships.put(owner),
    )
    of_another_company: tuple[Callable[[IdentityUnit], Awaitable[object]], ...] = (
        lambda unit: unit.branches.add(branch),
        lambda unit: unit.emission_points.add(point),
    )
    attempts: tuple[tuple[Scope, Callable[[IdentityUnit], Awaitable[object]]], ...] = (
        *((OrganizationScope(STRANGER.organization, None), write) for write in of_another_organization),
        *((sister.tenant, write) for write in of_another_company),
        *(
            (OrganizationScope(company.organization, None), write) for write in of_another_company
        ),  # a unit of no company at all
    )
    for scope, write_elsewhere in attempts:
        with pytest.raises(ForeignTenantError) as caught:
            await in_unit(identity_db, scope, write_elsewhere)
        assert caught.value.code == "foreign_tenant"
    kept = await in_unit(identity_db, company.tenant, lambda unit: queries.company(unit, company.id))
    assert kept == company


async def test_a_unit_s_time_is_its_transaction_s_for_what_it_records_and_announces(
    identity_db: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with identity_db.unit(OrganizationScope(FOUNDER.organization, None)) as transaction:
        unit = bind(transaction)
        company = await commands.create_company(
            unit, FOUNDER, tax_id=RNC, legal_name=parse_legal_name("Duarte")
        )
        began = MOMENT.validate_python(
            (await transaction.connection.execute(text("SELECT now()"))).scalar_one()
        )
    assert (unit.now, company.created_at, company.updated_at) == (began, began, began)
    cursor = await admin.execute("SELECT occurred_at FROM identity.outbox ORDER BY id")
    assert [row[0] for row in await cursor.fetchall()] == [began, began]


async def test_identity_s_state_is_never_read_without_a_scope(identity_db: BoundDatabase) -> None:
    async with identity_db.unit(None) as transaction:
        with pytest.raises(UnscopedUnitError) as caught:
            bind(transaction)
    assert caught.value.code == "unscoped_unit"


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_roles_are_granted_counted_listed_revoked_and_granted_again(
    identity_db: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(identity_db)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            identity_db,
            tenant,
            lambda unit: commands.grant_role(
                unit, company.id, subject=MEMBER.subject, role=role, actor=OWNER
            ),
        )

    granted = await grant(Role.VIEWER)
    assert granted.created
    changed = await grant(Role.ISSUER)
    assert (changed.created, changed.membership.version) == (False, 2)
    listed = await in_unit(
        identity_db, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest(size=1))
    )
    assert listed.next_cursor is not None
    assert await in_unit(identity_db, tenant, lambda unit: unit.memberships.owners(company.id)) == 1
    await in_unit(
        identity_db,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    revoked = await in_unit(
        identity_db, tenant, lambda unit: unit.memberships.get(company.id, MEMBER.subject)
    )
    assert revoked is not None
    assert (revoked.active, revoked.version, revoked.changed_by) == (False, 3, OWNER)
    assert await MembershipPermissions(identity_db).permissions(MEMBER, company.id) == frozenset()
    remaining = await in_unit(
        identity_db, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest())
    )
    assert [membership.subject for membership in remaining.items] == [OWNER]
    again = await grant(Role.VIEWER)
    assert (again.created, again.membership.version, again.membership.id) == (True, 4, granted.membership.id)
    assert (await published(admin))[-4:] == [
        "identity.membership.granted",
        "identity.membership.granted",
        "identity.membership.revoked",
        "identity.membership.granted",
    ]


async def test_two_owners_revoking_each_other_at_once_leave_one(
    identity_db: BoundDatabase, migrated: WorkerDatabase
) -> None:
    company = await create(identity_db)
    tenant = company.tenant
    await in_unit(
        identity_db,
        tenant,
        lambda unit: commands.grant_role(
            unit, company.id, subject=PARTNER.subject, role=Role.OWNER, actor=OWNER
        ),
    )
    holding, release = asyncio.Event(), asyncio.Event()

    async def first(unit: IdentityUnit) -> None:
        await unit.companies.locked(company.id)  # as the revocation does, before the other one can
        holding.set()
        await release.wait()
        await commands.revoke_role(unit, company.id, subject=PARTNER.subject, actor=OWNER)

    async def second(unit: IdentityUnit) -> None:
        await commands.revoke_role(unit, company.id, subject=OWNER, actor=PARTNER.subject)

    winner = asyncio.create_task(in_unit(identity_db, tenant, first))
    await holding.wait()
    loser = asyncio.create_task(in_unit(identity_db, tenant, second))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the second waits for the company
    assert not loser.done()
    release.set()
    await winner
    with pytest.raises(CompanyNotFoundError):  # its own role went first: it acts in the company no more
        await loser
    assert await in_unit(identity_db, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_an_owner_demoted_meanwhile_grants_with_the_role_it_holds_now(
    identity_db: BoundDatabase, migrated: WorkerDatabase
) -> None:
    company = await create(identity_db)
    tenant = company.tenant
    await in_unit(
        identity_db,
        tenant,
        lambda unit: commands.grant_role(
            unit, company.id, subject=PARTNER.subject, role=Role.OWNER, actor=OWNER
        ),
    )
    holding, release = asyncio.Event(), asyncio.Event()

    async def demote(unit: IdentityUnit) -> None:
        await unit.companies.locked(company.id)
        holding.set()
        await release.wait()
        await commands.grant_role(unit, company.id, subject=PARTNER.subject, role=Role.ADMIN, actor=OWNER)

    async def promote(unit: IdentityUnit) -> None:
        await commands.grant_role(
            unit, company.id, subject=MEMBER.subject, role=Role.OWNER, actor=PARTNER.subject
        )

    demotion = asyncio.create_task(in_unit(identity_db, tenant, demote))
    await holding.wait()
    promotion = asyncio.create_task(in_unit(identity_db, tenant, promote))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the promotion waits for the company
    release.set()
    await demotion
    with pytest.raises(OwnersOnlyError):  # checked by the route as an owner, decided as the admin it now is
        await promotion
    assert await in_unit(identity_db, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_permissions_come_from_the_membership_in_the_callers_organization(
    identity_db: BoundDatabase,
) -> None:
    company = await create(identity_db)
    permissions = MembershipPermissions(identity_db)
    assert await permissions.permissions(FOUNDER, company.id) == frozenset(Permission)
    assert await permissions.permissions(PARTNER, company.id) == frozenset()
    assert await permissions.permissions(FOUNDER, CompanyId(new_id())) == frozenset()


async def test_a_deactivated_company_lets_other_modules_read_only_and_identity_still_revoke(
    identity_db: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(identity_db)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            identity_db,
            tenant,
            lambda unit: commands.grant_role(
                unit, company.id, subject=MEMBER.subject, role=role, actor=OWNER
            ),
        )

    await grant(Role.ISSUER)
    await in_unit(
        identity_db,
        tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    for_other_services = await in_unit(
        identity_db,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.permissions_of(unit, company.id, OWNER),
    )
    assert for_other_services == READ_ONLY
    permissions = MembershipPermissions(identity_db)
    assert await permissions.permissions(FOUNDER, company.id) == frozenset(Permission)
    with pytest.raises(CompanyInactiveError):  # no role is granted there any more ...
        await grant(Role.VIEWER)
    await in_unit(  # ... but one is still taken away
        identity_db,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    assert await permissions.permissions(MEMBER, company.id) == frozenset()
    assert (await published(admin))[-2:] == ["identity.company.deactivated", "identity.membership.revoked"]


# ── Pages of branches and emission points ───────────────────────────────────────


async def test_branches_and_emission_points_are_paged_in_id_order_by_cursor(
    identity_db: BoundDatabase,
) -> None:
    company, first_branch, first_point = await a_company_with_a_branch_and_a_point(identity_db)
    tenant = company.tenant

    async def open_branch(code: str) -> Branch:
        return await in_unit(
            identity_db,
            tenant,
            lambda unit: commands.open_branch(
                unit, company.id, commands.Opening(parse_code(code), parse_name(code)), actor=OWNER
            ),
        )

    async def open_point(code: str) -> EmissionPoint:
        return await in_unit(
            identity_db,
            tenant,
            lambda unit: commands.open_emission_point(
                unit,
                company.id,
                first_branch.id,
                commands.Opening(parse_code(code), parse_name(code)),
                actor=OWNER,
            ),
        )

    async def branch_page(request: PageRequest) -> Page[Branch]:
        return await in_unit(identity_db, tenant, lambda unit: queries.branches(unit, company.id, request))

    async def point_page(request: PageRequest) -> Page[EmissionPoint]:
        return await in_unit(
            identity_db,
            tenant,
            lambda unit: queries.emission_points(unit, company.id, first_branch.id, request),
        )

    branches = sorted(
        [first_branch, await open_branch("SUC2"), await open_branch("SUC3")], key=lambda b: b.id
    )
    points = sorted([first_point, await open_point("002"), await open_point("003")], key=lambda p: p.id)
    first = await branch_page(PageRequest(size=2))
    assert first.next_cursor is not None
    rest = await branch_page(PageRequest(size=2, after=decode_cursor(first.next_cursor)))
    assert (first.items, rest.items, rest.next_cursor) == (tuple(branches[:2]), tuple(branches[2:]), None)
    first_points = await point_page(PageRequest(size=2))
    assert first_points.next_cursor is not None
    rest_points = await point_page(PageRequest(size=2, after=decode_cursor(first_points.next_cursor)))
    assert (first_points.items, rest_points.items, rest_points.next_cursor) == (
        tuple(points[:2]),
        tuple(points[2:]),
        None,
    )
    exactly_full = await point_page(PageRequest(size=3))
    assert (exactly_full.items, exactly_full.next_cursor) == (tuple(points), None)
