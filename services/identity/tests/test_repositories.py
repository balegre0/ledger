"""identity's state in PostgreSQL: repositories, units, the outbox and the permission source, for real."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Awaitable, Callable

import psycopg
import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
lazy from sqlalchemy import Insert
lazy from sqlalchemy.ext.asyncio import AsyncConnection

import ledger.events.identity.v1.events_pb2 as wire
from ledger_contracts.envelope import Envelope
from ledger_identity.adapters.events import IDENTITY, SOURCE, outgoing
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
from ledger_identity.domain.events import (
    CompanyCreated,
    CompanyDeactivated,
    CompanyUpdated,
    MembershipGranted,
    MembershipRevoked,
)
from ledger_identity.domain.membership import found
from ledger_identity.domain.organization import Organization
from ledger_identity.domain.roles import READ_ONLY, Role
from ledger_identity.domain.text import LegalName, parse_code, parse_legal_name, parse_name
from ledger_kernel.access import Permission, Principal, SubjectId
from ledger_kernel.countries.do import TaxId
from ledger_kernel.ids import CompanyId, OrganizationId, Tenant, new_id
from ledger_kernel.pagination import PageRequest, decode_cursor
from ledger_platform.authentication import organization_of
from ledger_platform.outbox import OutboxRecord
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.company import Company
lazy from ledger_identity.domain.events import IdentityEvent
lazy from ledger_kernel.pagination import Page
lazy from ledger_platform.database import Database, Scope
lazy from ledger_platform.outbox import Outbox

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FOUNDER: Final = Principal(
    subject=SubjectId("2001"), organization=organization_of("1001"), provider_organization="1001"
)
PARTNER: Final = Principal(
    subject=SubjectId("2002"), organization=FOUNDER.organization, provider_organization="1001"
)
STRANGER: Final = Principal(
    subject=SubjectId("2001"), organization=organization_of("1002"), provider_organization="1002"
)
OWNER: Final = FOUNDER.subject
MEMBER: Final = Principal(
    subject=SubjectId("3001"), organization=FOUNDER.organization, provider_organization="1001"
)
RNC: Final = TaxId.parse("101850043")
ROW_LEVEL_SECURITY_OFF: Final = tuple(
    text(statement)
    for statement in (
        "ALTER TABLE organizations DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE companies DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE branches DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE emission_points DISABLE ROW LEVEL SECURITY",
    )
)
"""What a dropped policy would leave: run inside a transaction that rolls back (DDL is transactional)."""


class UndoneError(Exception):
    """Raised on purpose, so that the transaction it leaves rolls back everything it did."""


def anything(version: int) -> bool:
    del version
    return True


async def in_unit[T](
    database: Database, outbox: Outbox, scope: Scope, work: Callable[[IdentityUnit], Awaitable[T]]
) -> T:
    async with database.transaction(scope) as transaction:
        return await work(bind(transaction, outbox, clock=lambda: NOW))


async def create(
    database: Database, outbox: Outbox, founder: Principal = FOUNDER, tax_id: TaxId = RNC
) -> Company:
    return await in_unit(
        database,
        outbox,
        founder.organization,
        lambda unit: commands.create_company(
            unit, founder, tax_id=tax_id, legal_name=parse_legal_name("Duarte")
        ),
    )


async def write(database: Database, scope: Scope, statement: Insert) -> None:
    async with database.transaction(scope) as unit:
        await unit.connection.execute(statement)


async def published(database: Database) -> list[Envelope]:
    async with database.transaction(None) as unit:
        rows = await unit.connection.execute(
            select(OutboxRecord.headers, OutboxRecord.data).order_by(OutboxRecord.created_at, OutboxRecord.id)
        )
        return [Envelope.from_headers(headers, data) for headers, data in rows]


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_company_is_kept_with_its_owner_and_organization(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    read = await in_unit(database, outbox, tenant, lambda unit: queries.company(unit, company.id))
    assert read == company
    organization = await in_unit(
        database, outbox, tenant, lambda unit: queries.organization(unit, tenant.organization)
    )
    assert (organization.id, organization.provider_id) == (FOUNDER.organization, "1001")
    permissions = await in_unit(
        database, outbox, tenant, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert permissions == frozenset(Permission)


async def test_what_a_unit_changes_is_announced_through_the_outbox_when_it_commits(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    created, granted = await published(database)
    assert (created.type, created.source, created.tenant) == (
        "ledger.events.identity.v1.CompanyCreated",
        SOURCE,
        company.tenant,
    )
    payload = created.unwrap(wire.CompanyCreated)
    assert (
        payload.company_id,
        payload.organization_id,
        payload.tax_id,
        payload.legal_name,
        payload.version,
    ) == (
        str(company.id),
        str(company.organization),
        "101850043",
        "Duarte",
        1,
    )
    assert granted.unwrap(wire.MembershipGranted).role == wire.ROLE_OWNER


async def test_a_unit_that_fails_keeps_and_announces_nothing(database: Database, outbox: Outbox) -> None:
    async def create_then_fail(unit: IdentityUnit) -> None:
        await commands.create_company(unit, FOUNDER, tax_id=RNC, legal_name=parse_legal_name("Duarte"))
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await in_unit(database, outbox, FOUNDER.organization, create_then_fail)
    async with database.transaction(FOUNDER.organization) as unit:  # the scope that would see the company
        companies = (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one()
    assert (companies, await published(database)) == (0, [])


async def test_a_tax_id_is_unique_within_an_organization_only(database: Database, outbox: Outbox) -> None:
    await create(database, outbox)
    await create(database, outbox, founder=STRANGER)
    with pytest.raises(TaxIdTakenError):
        await create(database, outbox)


async def test_another_organization_sees_none_of_a_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(database, outbox, STRANGER.organization, lambda unit: queries.company(unit, company.id))
    stranger_tenant = Tenant(STRANGER.organization, company.id)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(database, outbox, stranger_tenant, lambda unit: queries.company(unit, company.id))
    assert await MembershipPermissions(database).permissions(STRANGER, company.id) == frozenset()


async def test_a_change_applies_to_the_version_it_was_made_from_only(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    renamed, _ = company.rename(parse_legal_name("Duarte Hermanos"), now=NOW)
    assert await in_unit(
        database, outbox, company.tenant, lambda unit: unit.companies.update(renamed, version=1)
    )
    again, _ = company.rename(parse_legal_name("Duarte e Hijos"), now=NOW)
    assert not await in_unit(
        database, outbox, company.tenant, lambda unit: unit.companies.update(again, version=1)
    )
    assert (
        await in_unit(database, outbox, company.tenant, lambda unit: queries.company(unit, company.id))
    ).legal_name == ("Duarte Hermanos")


async def test_renames_and_deactivations_are_announced(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.rename_company(
            unit, company.id, legal_name=parse_legal_name("Duarte S.A."), admits=anything, actor=OWNER
        ),
    )
    await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    types = [envelope.type.rsplit(".", 1)[1] for envelope in await published(database)]
    assert types == ["CompanyCreated", "MembershipGranted", "CompanyUpdated", "CompanyDeactivated"]


async def test_a_principal_pages_through_the_companies_it_holds_a_role_in(
    database: Database, outbox: Outbox
) -> None:
    for rnc in ("101850043", "130000012", "401007551"):
        await create(database, outbox, tax_id=TaxId.parse(rnc))
    await create(database, outbox, founder=STRANGER)
    first = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2)),
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None
    after = first.items[-1].id
    rest = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2, after=after)),
    )
    assert (len(rest.items), rest.next_cursor) == (1, None)
    assert all(company.organization == FOUNDER.organization for company in (*first.items, *rest.items))
    partner = await in_unit(
        database,
        outbox,
        FOUNDER.organization,
        lambda unit: queries.companies_of(unit, PARTNER.subject, PageRequest()),
    )
    assert partner.items == ()


# ── Branches and emission points ───────────────────────────────────────────────


async def test_branches_and_emission_points_are_kept_per_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    other = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, code=parse_code("SUC1"), name=parse_name("Santiago")
        ),
    )
    await in_unit(
        database,
        outbox,
        other.tenant,
        lambda unit: commands.open_branch(unit, other.id, code=parse_code("SUC1"), name=parse_name("Bonao")),
    )
    with pytest.raises(BranchCodeTakenError):
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: commands.open_branch(
                unit, company.id, code=parse_code("SUC1"), name=parse_name("X")
            ),
        )
    point = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
        ),
    )
    with pytest.raises(EmissionPointCodeTakenError):
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: commands.open_emission_point(
                unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Y")
            ),
        )
    assert (
        await in_unit(
            database, outbox, company.tenant, lambda unit: queries.branch(unit, company.id, branch.id)
        )
        == branch
    )
    assert (
        await in_unit(
            database,
            outbox,
            company.tenant,
            lambda unit: queries.emission_point(unit, company.id, branch.id, point.id),
        )
        == point
    )
    branches = await in_unit(
        database, outbox, company.tenant, lambda unit: queries.branches(unit, company.id, PageRequest())
    )
    points = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: queries.emission_points(unit, company.id, branch.id, PageRequest()),
    )
    assert (branches.items, points.items) == ((branch,), (point,))


async def test_a_company_scope_sees_no_branch_of_another_company(database: Database, outbox: Outbox) -> None:
    company = await create(database, outbox)
    other = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(
            unit, company.id, code=parse_code("SUC1"), name=parse_name("Santiago")
        ),
    )
    seen_by_other = await in_unit(
        database, outbox, other.tenant, lambda unit: unit.branches.page(company.id, PageRequest())
    )
    asked_wrongly = await in_unit(
        database, outbox, company.tenant, lambda unit: unit.branches.get(other.id, branch.id)
    )
    assert (seen_by_other.items, asked_wrongly) == ((), None)


# ── Tenancy checked twice ───────────────────────────────────────────────────────


async def test_a_row_cannot_point_at_a_company_or_a_branch_of_another_tenant(
    database: Database, outbox: Outbox
) -> None:
    mine = await create(database, outbox)
    sister = await create(database, outbox, tax_id=TaxId.parse("130000012"))
    theirs = await create(database, outbox, founder=STRANGER)
    branch = await in_unit(
        database,
        outbox,
        sister.tenant,
        lambda unit: commands.open_branch(unit, sister.id, code=parse_code("SUC1"), name=parse_name("Bonao")),
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
        (mine.organization, member_of_theirs),  # row-level security lets each of these through
        (Tenant(mine.organization, theirs.id), branch_of_theirs),
        (mine.tenant, point_in_the_sister_s_branch),
    )
    for scope, statement in attempts:
        with pytest.raises(IntegrityError) as caught:
            await write(database, scope, statement)
        assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


async def with_the_policies_off[T](
    database: Database,
    outbox: Outbox,
    scope: Scope,
    look: Callable[[IdentityUnit, AsyncConnection], Awaitable[T]],
) -> T:
    """What ``look`` sees in a unit of ``scope`` with row-level security off: the application filter alone."""
    seen: list[T] = []

    async def look_then_undo() -> None:
        async with database.transaction(scope) as transaction:
            for statement in ROW_LEVEL_SECURITY_OFF:
                await transaction.connection.execute(statement)
            seen.append(await look(bind(transaction, outbox, clock=lambda: NOW), transaction.connection))
            raise UndoneError  # the policies come back with the rollback

    with pytest.raises(UndoneError):
        await look_then_undo()
    return seen[0]


async def a_company_with_a_branch_and_a_point(
    database: Database, outbox: Outbox
) -> tuple[Company, Branch, EmissionPoint]:
    company = await create(database, outbox)
    branch = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_branch(unit, company.id, code=parse_code("SUC1"), name=parse_name("S")),
    )
    point = await in_unit(
        database,
        outbox,
        company.tenant,
        lambda unit: commands.open_emission_point(
            unit, company.id, branch.id, code=parse_code("001"), name=parse_name("Caja")
        ),
    )
    return company, branch, point


async def test_the_repositories_keep_to_the_unit_s_organization_without_row_level_security(
    database: Database, outbox: Outbox
) -> None:
    company, branch, point = await a_company_with_a_branch_and_a_point(database, outbox)

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

    seen = await with_the_policies_off(database, outbox, STRANGER.organization, look)
    assert seen == [1, None, None, None, (), None, 0, (), None, (), None, ()]


async def test_the_repositories_keep_to_the_unit_s_company_without_row_level_security(
    database: Database, outbox: Outbox
) -> None:
    company, branch, point = await a_company_with_a_branch_and_a_point(database, outbox)
    sister = await create(database, outbox, tax_id=TaxId.parse("130000012"))

    async def look(unit: IdentityUnit, connection: AsyncConnection) -> list[object]:
        del connection
        return [
            await unit.branches.get(company.id, branch.id),
            (await unit.branches.page(company.id, PageRequest())).items,
            await unit.emission_points.get(company.id, point.id),
            (await unit.emission_points.page(company.id, branch.id, PageRequest())).items,
        ]

    nothing: Final = [None, (), None, ()]
    assert await with_the_policies_off(database, outbox, sister.tenant, look) == nothing
    assert await with_the_policies_off(database, outbox, company.organization, look) == nothing
    own = await with_the_policies_off(database, outbox, company.tenant, look)
    assert own == [branch, (branch,), point, (point,)]  # the same statements, in the company's own unit


async def test_a_unit_refuses_to_write_a_row_of_another_tenant(database: Database, outbox: Outbox) -> None:
    """Row-level security would refuse each of these too: the repositories do not count on it."""
    company = await create(database, outbox)
    sister = await create(database, outbox, tax_id=TaxId.parse("130000012"))
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
        *((STRANGER.organization, write) for write in of_another_organization),
        *((sister.tenant, write) for write in of_another_company),
        *((company.organization, write) for write in of_another_company),  # a unit of no company at all
    )
    for scope, write_elsewhere in attempts:
        with pytest.raises(ForeignTenantError) as caught:
            await in_unit(database, outbox, scope, write_elsewhere)
        assert caught.value.code == "foreign_tenant"
    kept = await in_unit(database, outbox, company.tenant, lambda unit: queries.company(unit, company.id))
    assert kept == company


async def test_a_unit_reads_its_clock_once_for_its_time_and_its_events(
    database: Database, outbox: Outbox
) -> None:
    readings: list[datetime] = []

    def ticking() -> datetime:
        readings.append(NOW + timedelta(seconds=len(readings)))
        return readings[-1]

    async with database.transaction(FOUNDER.organization) as transaction:
        unit = bind(transaction, outbox, clock=ticking)
        company = await commands.create_company(
            unit, FOUNDER, tax_id=RNC, legal_name=parse_legal_name("Duarte")
        )
    assert (readings, company.created_at) == ([NOW], NOW)
    assert [envelope.time for envelope in await published(database)] == [NOW, NOW]


async def test_identity_s_state_is_never_read_without_a_scope(database: Database, outbox: Outbox) -> None:
    async with database.transaction(None) as transaction:
        with pytest.raises(UnscopedUnitError) as caught:
            bind(transaction, outbox)
    assert caught.value.code == "unscoped_unit"


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_roles_are_granted_counted_listed_revoked_and_granted_again(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            database,
            outbox,
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
        database, outbox, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest(size=1))
    )
    assert listed.next_cursor is not None
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1
    await in_unit(
        database,
        outbox,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    revoked = await in_unit(
        database, outbox, tenant, lambda unit: unit.memberships.get(company.id, MEMBER.subject)
    )
    assert revoked is not None
    assert (revoked.active, revoked.version, revoked.changed_by) == (False, 3, OWNER)
    assert await MembershipPermissions(database).permissions(MEMBER, company.id) == frozenset()
    remaining = await in_unit(
        database, outbox, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest())
    )
    assert [membership.subject for membership in remaining.items] == [OWNER]
    again = await grant(Role.VIEWER)
    assert (again.created, again.membership.version, again.membership.id) == (True, 4, granted.membership.id)
    types = [envelope.type.rsplit(".", 1)[1] for envelope in await published(database)]
    assert types[-4:] == ["MembershipGranted", "MembershipGranted", "MembershipRevoked", "MembershipGranted"]


async def test_two_owners_revoking_each_other_at_once_leave_one(
    database: Database, database_url: str, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    await in_unit(
        database,
        outbox,
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

    winner = asyncio.create_task(in_unit(database, outbox, tenant, first))
    await holding.wait()
    loser = asyncio.create_task(in_unit(database, outbox, tenant, second))
    await eventually(lambda: waits_for_a_lock(database_url))  # the second waits for the company
    assert not loser.done()
    release.set()
    await winner
    with pytest.raises(CompanyNotFoundError):  # its own role went first: it acts in the company no more
        await loser
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_an_owner_demoted_meanwhile_grants_with_the_role_it_holds_now(
    database: Database, database_url: str, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant
    await in_unit(
        database,
        outbox,
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

    demotion = asyncio.create_task(in_unit(database, outbox, tenant, demote))
    await holding.wait()
    promotion = asyncio.create_task(in_unit(database, outbox, tenant, promote))
    await eventually(lambda: waits_for_a_lock(database_url))  # the promotion waits for the company
    release.set()
    await demotion
    with pytest.raises(OwnersOnlyError):  # checked by the route as an owner, decided as the admin it now is
        await promotion
    assert await in_unit(database, outbox, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_permissions_come_from_the_membership_in_the_callers_organization(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    permissions = MembershipPermissions(database)
    assert await permissions.permissions(FOUNDER, company.id) == frozenset(Permission)
    assert await permissions.permissions(PARTNER, company.id) == frozenset()
    assert await permissions.permissions(FOUNDER, CompanyId(new_id())) == frozenset()


async def test_a_deactivated_company_lets_other_services_read_only_and_identity_still_revoke(
    database: Database, outbox: Outbox
) -> None:
    company = await create(database, outbox)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            database,
            outbox,
            tenant,
            lambda unit: commands.grant_role(
                unit, company.id, subject=MEMBER.subject, role=role, actor=OWNER
            ),
        )

    await grant(Role.ISSUER)
    await in_unit(
        database,
        outbox,
        tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    for_other_services = await in_unit(
        database, outbox, FOUNDER.organization, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert for_other_services == READ_ONLY
    permissions = MembershipPermissions(database)
    assert await permissions.permissions(FOUNDER, company.id) == frozenset(Permission)
    with pytest.raises(CompanyInactiveError):  # no role is granted there any more ...
        await grant(Role.VIEWER)
    await in_unit(  # ... but one is still taken away
        database,
        outbox,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    assert await permissions.permissions(MEMBER, company.id) == frozenset()
    types = [envelope.type.rsplit(".", 1)[1] for envelope in await published(database)]
    assert types[-2:] == ["CompanyDeactivated", "MembershipRevoked"]


# ── Pages of branches and emission points ───────────────────────────────────────


async def test_branches_and_emission_points_are_paged_in_id_order_by_cursor(
    database: Database, outbox: Outbox
) -> None:
    company, first_branch, first_point = await a_company_with_a_branch_and_a_point(database, outbox)
    tenant = company.tenant

    async def open_branch(code: str) -> Branch:
        return await in_unit(
            database,
            outbox,
            tenant,
            lambda unit: commands.open_branch(unit, company.id, code=parse_code(code), name=parse_name(code)),
        )

    async def open_point(code: str) -> EmissionPoint:
        return await in_unit(
            database,
            outbox,
            tenant,
            lambda unit: commands.open_emission_point(
                unit, company.id, first_branch.id, code=parse_code(code), name=parse_name(code)
            ),
        )

    async def branch_page(request: PageRequest) -> Page[Branch]:
        return await in_unit(
            database, outbox, tenant, lambda unit: queries.branches(unit, company.id, request)
        )

    async def point_page(request: PageRequest) -> Page[EmissionPoint]:
        return await in_unit(
            database,
            outbox,
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


# ── The wire ────────────────────────────────────────────────────────────────────

TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()))


@pytest.mark.parametrize(
    ("event", "subject"),
    [
        (
            CompanyCreated(tenant=TENANT, tax_id=RNC, legal_name=LegalName("Duarte"), version=1),
            "identity.company.created",
        ),
        (
            CompanyUpdated(tenant=TENANT, tax_id=RNC, legal_name=LegalName("Duarte II"), version=2),
            "identity.company.updated",
        ),
        (CompanyDeactivated(tenant=TENANT, version=3), "identity.company.deactivated"),
        (
            MembershipGranted(
                tenant=TENANT, subject=SubjectId("3001"), role=Role.ISSUER, previous=Role.VIEWER, version=2
            ),
            "identity.membership.granted",
        ),
        (
            MembershipRevoked(tenant=TENANT, subject=SubjectId("3001"), role=Role.ISSUER, version=3),
            "identity.membership.revoked",
        ),
    ],
    ids=["created", "updated", "deactivated", "granted", "revoked"],
)
def test_every_event_has_its_subject_in_the_identity_stream(event: IdentityEvent, subject: str) -> None:
    message, published_on = outgoing(event)
    assert published_on == subject
    assert published_on.startswith("identity.")
    assert IDENTITY.subjects == ("identity.>",)
    assert message.DESCRIPTOR.full_name == f"ledger.events.identity.v1.{type(event).__name__}"


def test_the_wire_says_what_the_domain_says() -> None:
    granted, _ = outgoing(
        MembershipGranted(tenant=TENANT, subject=SubjectId("3001"), role=Role.OWNER, previous=None, version=1)
    )
    assert isinstance(granted, wire.MembershipGranted)
    assert (
        granted.company_id,
        granted.organization_id,
        granted.subject,
        granted.role,
        granted.previous_role,
        granted.version,
    ) == (
        str(TENANT.company),
        str(TENANT.organization),
        "3001",
        wire.ROLE_OWNER,
        wire.ROLE_UNSPECIFIED,
        1,
    )
    revoked, _ = outgoing(
        MembershipRevoked(tenant=TENANT, subject=SubjectId("3001"), role=Role.ACCOUNTANT, version=4)
    )
    assert isinstance(revoked, wire.MembershipRevoked)
    assert revoked.role == wire.ROLE_ACCOUNTANT
    deactivated, _ = outgoing(CompanyDeactivated(tenant=TENANT, version=5))
    assert isinstance(deactivated, wire.CompanyDeactivated)
    assert deactivated.version == 5
