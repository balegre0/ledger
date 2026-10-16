"""identity's state in PostgreSQL: repositories, units, the outbox and the permission source, for real.

Units are identity's own (``module_database``: ``identity_rw``, its schema); what they announced is read from
``identity.outbox`` past row-level security (``admin``).
"""

import asyncio
from datetime import UTC, datetime
from typing import Final, final
lazy from collections.abc import Awaitable, Callable
lazy from uuid import UUID

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
    CompanyRecord,
    EstablishmentRecord,
    MembershipRecord,
    TaxRegistrationRecord,
)
from ledger_identity.adapters.postgres.unit import UnscopedUnitError, bind
from ledger_identity.application import commands, queries
from ledger_identity.domain.errors import (
    CompanyInactiveError,
    EstablishmentCodeTakenError,
    OwnersOnlyError,
    TaxIdTakenError,
)
from ledger_identity.domain.establishments import MAIN_CODE, Establishment
from ledger_identity.domain.membership import found
from ledger_identity.domain.organization import ORGANIZATION_ADMIN, Organization
from ledger_identity.domain.roles import EVERYTHING, READ_ONLY
from ledger_identity.domain.text import parse_code, parse_legal_name, parse_name, parse_time_zone
from ledger_kernel.access import CompanyNotFoundError, Principal, Role, SubjectId
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.pagination import PageRequest, decode_cursor
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_testing.database import app_url
from ledger_testing.waiting import eventually, waits_for_a_lock
lazy from ledger_identity.application.ports import IdentityUnit
lazy from ledger_identity.domain.company import Company, FiscalProfile
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
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
SANTO_DOMINGO: Final = parse_time_zone("America/Santo_Domingo")
THEIRS: Final = TaxId(TaxIdScheme("do.rnc"), "401500022")
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
    """What identity's units signalled after their commits (``module_database`` signals here)."""
    return RecordedSignals()


def anything(version: int) -> bool:
    del version
    return True


async def in_unit[T](
    module_database: BoundDatabase, scope: Scope, work: Callable[[IdentityUnit], Awaitable[T]]
) -> T:
    async with module_database.unit(scope) as unit:
        return await work(bind(unit))


def profile(tax_id: TaxId = RNC, *also: TaxId) -> FiscalProfile:
    """A Dominican company's profile under ``tax_id`` (its primary one) and ``also``."""
    return FiscalProfile(
        jurisdiction=parse_jurisdiction("DO"),
        functional_currency=Currency.of("DOP"),
        tax_ids=(tax_id, *also),
    )


async def create(
    module_database: BoundDatabase,
    founder: Principal = FOUNDER,
    tax_id: TaxId = RNC,
    *also: TaxId,
) -> Company:
    return await in_unit(
        module_database,
        OrganizationScope(founder.organization, None),
        lambda unit: commands.create_company(
            unit,
            founder,
            profile=profile(tax_id, *also),
            legal_name=parse_legal_name("Duarte"),
            time_zone=SANTO_DOMINGO,
        ),
    )


async def write(module_database: BoundDatabase, scope: Scope, statement: Insert) -> None:
    async with module_database.unit(scope) as unit:
        await unit.connection.execute(statement)


async def published(admin: psycopg.AsyncConnection) -> list[str]:
    """The types of the events identity's outbox holds, oldest first."""
    cursor = await admin.execute("SELECT type FROM identity.outbox ORDER BY id")
    return [kind for (kind,) in TYPES.validate_python(await cursor.fetchall())]


# ── Companies ───────────────────────────────────────────────────────────────────


async def test_a_company_is_kept_with_its_owner_and_organization(module_database: BoundDatabase) -> None:
    company = await create(module_database)
    tenant = company.tenant
    read = await in_unit(module_database, tenant, lambda unit: queries.company(unit, company.id))
    assert read == company
    organization = await in_unit(
        module_database, tenant, lambda unit: queries.organization(unit, tenant.organization)
    )
    assert (organization.id, organization.provider_id) == (FOUNDER.organization, "1001")
    permissions = await in_unit(
        module_database, tenant, lambda unit: queries.permissions_of(unit, company.id, OWNER)
    )
    assert permissions == EVERYTHING


async def test_what_a_unit_changes_is_announced_through_the_outbox_when_it_commits(
    module_database: BoundDatabase, signals: RecordedSignals, admin: psycopg.AsyncConnection
) -> None:
    company = await create(module_database)
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
    module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async def create_then_fail(unit: IdentityUnit) -> None:
        await commands.create_company(
            unit, FOUNDER, profile=profile(), legal_name=parse_legal_name("Duarte"), time_zone=SANTO_DOMINGO
        )
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await in_unit(module_database, OrganizationScope(FOUNDER.organization, None), create_then_fail)
    async with module_database.unit(
        OrganizationScope(FOUNDER.organization, None)
    ) as unit:  # the scope that would see the company
        companies = (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one()
    assert (companies, await published(admin)) == (0, [])


async def test_a_tax_id_is_one_active_company_s_in_every_organization(module_database: BoundDatabase) -> None:
    """One RNC is one taxpayer: another organization cannot register it, though it sees none of the first's
    rows (the unique index enforces it whatever the policies show)."""
    await create(module_database)
    with pytest.raises(TaxIdTakenError):
        await create(module_database)
    with pytest.raises(TaxIdTakenError):
        await create(module_database, founder=STRANGER)
    async with module_database.unit(OrganizationScope(STRANGER.organization, None)) as unit:
        assert (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one() == 0


async def test_a_tax_id_is_unique_by_its_scheme_and_its_value(module_database: BoundDatabase) -> None:
    """The same characters under another scheme are another taxpayer's: they never collide."""
    rnc = await create(module_database)
    cedula = await create(
        module_database, founder=STRANGER, tax_id=TaxId(TaxIdScheme("do.cedula"), RNC.value)
    )
    assert (rnc.profile.primary_tax_id, cedula.profile.primary_tax_id) == (
        RNC,
        TaxId(TaxIdScheme("do.cedula"), "101850043"),
    )
    read = await in_unit(module_database, cedula.tenant, lambda unit: queries.company(unit, cedula.id))
    assert read.profile == cedula.profile
    with pytest.raises(TaxIdTakenError):
        await create(module_database, founder=STRANGER, tax_id=TaxId(TaxIdScheme("do.cedula"), RNC.value))


async def registered_ids(module_database: BoundDatabase) -> list[tuple[str, str, UUID, UUID]]:
    """The global registry's rows, as any unit reads them (it is no tenant's)."""
    async with module_database.unit(OrganizationScope(STRANGER.organization, None)) as unit:
        rows = await unit.connection.execute(
            select(
                TaxRegistrationRecord.scheme,
                TaxRegistrationRecord.value,
                TaxRegistrationRecord.organization_id,
                TaxRegistrationRecord.company_id,
            ).order_by(TaxRegistrationRecord.scheme, TaxRegistrationRecord.value)
        )
        return [(row[0], row[1], row[2], row[3]) for row in rows.all()]


async def test_an_active_company_is_in_the_global_registry_and_deactivating_it_releases_its_tax_id(
    module_database: BoundDatabase,
) -> None:
    """Another organization sees the registry, though it sees none of the company's own rows."""
    company = await create(module_database)
    assert await registered_ids(module_database) == [
        ("do.rnc", "101850043", company.organization, company.id)
    ]
    await deactivate(module_database, company)
    assert await registered_ids(module_database) == []


async def test_a_refused_registration_leaves_neither_the_company_nor_a_registry_row(
    module_database: BoundDatabase,
) -> None:
    first = await create(module_database)
    with pytest.raises(TaxIdTakenError):
        await create(module_database, founder=STRANGER)
    assert await registered_ids(module_database) == [("do.rnc", "101850043", first.organization, first.id)]


CEDULA: Final = TaxId(TaxIdScheme("do.cedula"), "00113918205")


async def test_a_company_keeps_its_profile_and_every_tax_id_in_order(module_database: BoundDatabase) -> None:
    company = await create(module_database, FOUNDER, RNC, CEDULA)
    read = await in_unit(module_database, company.tenant, lambda unit: queries.company(unit, company.id))
    assert read == company
    assert read.profile.tax_ids == (RNC, CEDULA)  # the first stays the primary one
    assert (read.profile.jurisdiction, read.profile.functional_currency) == (
        parse_jurisdiction("DO"),
        Currency.of("DOP"),
    )
    assert await registered_ids(module_database) == [
        ("do.cedula", "00113918205", company.organization, company.id),
        ("do.rnc", "101850043", company.organization, company.id),
    ]


async def test_any_one_tax_id_of_a_company_taken_refuses_it_whole(module_database: BoundDatabase) -> None:
    first = await create(module_database)
    with pytest.raises(TaxIdTakenError):
        await create(module_database, STRANGER, CEDULA, RNC)
    assert await registered_ids(module_database) == [("do.rnc", "101850043", first.organization, first.id)]
    async with module_database.unit(OrganizationScope(STRANGER.organization, None)) as unit:
        assert (
            await unit.connection.execute(select(func.count()).select_from(CompanyRecord))
        ).scalar_one() == 0


async def test_deactivating_a_company_releases_all_its_tax_ids(module_database: BoundDatabase) -> None:
    company = await create(module_database, FOUNDER, RNC, CEDULA)
    await deactivate(module_database, company)
    assert await registered_ids(module_database) == []
    again = await create(module_database, STRANGER, CEDULA)
    assert again.profile.primary_tax_id == CEDULA


async def test_a_locked_fiscal_profile_is_kept_at_its_new_version(module_database: BoundDatabase) -> None:
    company = await create(module_database)
    locked, _ = company.lock_fiscal_profile(now=NOW)

    async def lock(unit: IdentityUnit) -> bool:
        return await unit.companies.update(locked, version=company.version)

    assert await in_unit(module_database, company.tenant, lock)
    read = await in_unit(module_database, company.tenant, lambda unit: queries.company(unit, company.id))
    assert (read.fiscal_profile_locked_at, read.version, read.profile) == (NOW, 2, company.profile)


async def test_two_organizations_registering_one_taxpayer_at_once_get_one_company(
    module_database: BoundDatabase,
) -> None:
    outcomes = await asyncio.gather(
        create(module_database), create(module_database, founder=STRANGER), return_exceptions=True
    )
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ["Company", "TaxIdTakenError"]


async def deactivate(module_database: BoundDatabase, company: Company) -> None:
    tenant = Tenant(company.organization, company.id, None)
    await in_unit(
        module_database,
        tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=lambda _: True, actor=OWNER),
    )


async def test_a_deactivated_company_frees_its_tax_id_for_any_organization(
    module_database: BoundDatabase,
) -> None:
    first = await create(module_database)
    await deactivate(module_database, first)
    again = await create(module_database)  # the same organization registers the taxpayer again
    await deactivate(module_database, again)
    theirs = await create(module_database, founder=STRANGER)  # or another one does
    assert len({first.id, again.id, theirs.id}) == 3
    with pytest.raises(TaxIdTakenError):
        await create(module_database)


async def test_another_organization_sees_none_of_a_company(module_database: BoundDatabase) -> None:
    company = await create(module_database)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(
            module_database,
            OrganizationScope(STRANGER.organization, None),
            lambda unit: queries.company(unit, company.id),
        )
    stranger_tenant = Tenant(STRANGER.organization, company.id, None)
    with pytest.raises(CompanyNotFoundError):
        await in_unit(module_database, stranger_tenant, lambda unit: queries.company(unit, company.id))
    assert await MembershipPermissions(module_database).permissions(STRANGER, company.id) == frozenset()


async def test_a_change_applies_to_the_version_it_was_made_from_only(module_database: BoundDatabase) -> None:
    company = await create(module_database)
    renamed, _ = company.rename(parse_legal_name("Duarte Hermanos"), now=NOW)
    assert await in_unit(
        module_database, company.tenant, lambda unit: unit.companies.update(renamed, version=1)
    )
    again, _ = company.rename(parse_legal_name("Duarte e Hijos"), now=NOW)
    assert not await in_unit(
        module_database, company.tenant, lambda unit: unit.companies.update(again, version=1)
    )
    assert (
        await in_unit(module_database, company.tenant, lambda unit: queries.company(unit, company.id))
    ).legal_name == ("Duarte Hermanos")


async def test_renames_and_deactivations_are_announced(
    module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(module_database)
    await in_unit(
        module_database,
        company.tenant,
        lambda unit: commands.rename_company(
            unit, company.id, legal_name=parse_legal_name("Duarte S.A."), admits=anything, actor=OWNER
        ),
    )
    await in_unit(
        module_database,
        company.tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    assert await published(admin) == [
        "identity.company.registered",
        "identity.membership.granted",
        "identity.company.updated",
        "identity.company.deactivated",
    ]


async def test_a_principal_pages_through_the_companies_it_holds_a_role_in(
    module_database: BoundDatabase,
) -> None:
    for rnc in ("101850043", "130000012", "401007551"):
        await create(module_database, tax_id=TaxId(TaxIdScheme("do.rnc"), rnc))
    await create(module_database, founder=STRANGER, tax_id=THEIRS)
    first = await in_unit(
        module_database,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2)),
    )
    assert len(first.items) == 2
    assert first.next_cursor is not None
    after = first.items[-1].id
    rest = await in_unit(
        module_database,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, OWNER, PageRequest(size=2, after=after)),
    )
    assert (len(rest.items), rest.next_cursor) == (1, None)
    assert all(company.organization == FOUNDER.organization for company in (*first.items, *rest.items))
    partner = await in_unit(
        module_database,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.companies_of(unit, PARTNER.subject, PageRequest()),
    )
    assert partner.items == ()


# ── Establishments ─────────────────────────────────────────────────────────────


async def open_in(
    module_database: BoundDatabase, company: Company, code: str, *, time_zone: str | None = None
) -> Establishment:
    """The establishment ``OWNER`` opens in ``company`` under ``code``, named after it."""
    opening = commands.Opening(
        parse_code(code), parse_name(code), None if time_zone is None else parse_time_zone(time_zone)
    )
    return await in_unit(
        module_database,
        company.tenant,
        lambda unit: commands.open_establishment(unit, company.id, opening, actor=OWNER),
    )


async def main_of(module_database: BoundDatabase, company: Company) -> Establishment:
    return await in_unit(
        module_database, company.tenant, lambda unit: queries.main_establishment(unit, company.id)
    )


async def test_a_company_is_registered_with_its_main_establishment_in_the_zone_it_was_given(
    module_database: BoundDatabase,
) -> None:
    company = await create(module_database)
    main = await main_of(module_database, company)
    assert (main.code, main.tenant, main.time_zone, main.created_at) == (
        MAIN_CODE,
        company.tenant,
        SANTO_DOMINGO,
        company.created_at,
    )
    assert (
        await in_unit(
            module_database, company.tenant, lambda unit: queries.establishment(unit, company.id, main.id)
        )
        == main
    )


async def test_establishments_are_kept_per_company_under_codes_unique_in_it(
    module_database: BoundDatabase,
) -> None:
    company = await create(module_database)
    other = await create(module_database, tax_id=TaxId(TaxIdScheme("do.rnc"), "130000012"))
    santiago = await open_in(module_database, company, "SUC1")
    await open_in(module_database, other, "SUC1")  # another company's code is free
    for code in ("SUC1", "MAIN"):
        with pytest.raises(EstablishmentCodeTakenError):
            await open_in(module_database, company, code)
    assert santiago.time_zone == SANTO_DOMINGO  # the main one's, when none is named
    madrid = await open_in(module_database, company, "SUC2", time_zone="Europe/Madrid")
    assert madrid.time_zone == parse_time_zone("Europe/Madrid")
    listed = await in_unit(
        module_database, company.tenant, lambda unit: queries.establishments(unit, company.id, PageRequest())
    )
    main = await main_of(module_database, company)
    assert {item.code for item in listed.items} == {"MAIN", "SUC1", "SUC2"}
    assert main in listed.items
    assert santiago in listed.items


async def test_another_organization_sees_no_establishment(module_database: BoundDatabase) -> None:
    company = await create(module_database)
    santiago = await open_in(module_database, company, "SUC1")
    theirs = await create(module_database, founder=STRANGER, tax_id=THEIRS)
    seen_by_theirs = await in_unit(
        module_database, theirs.tenant, lambda unit: unit.establishments.page(company.id, PageRequest())
    )
    asked_wrongly = await in_unit(
        module_database, theirs.tenant, lambda unit: unit.establishments.get(company.id, santiago.id)
    )
    by_code = await in_unit(
        module_database, theirs.tenant, lambda unit: unit.establishments.by_code(company.id, MAIN_CODE)
    )
    assert (seen_by_theirs.items, asked_wrongly, by_code) == ((), None, None)


# ── Tenancy checked twice ───────────────────────────────────────────────────────


async def test_a_row_cannot_point_at_a_company_of_another_tenant(
    module_database: BoundDatabase,
) -> None:
    mine = await create(module_database)
    theirs = await create(module_database, founder=STRANGER, tax_id=THEIRS)
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
    establishment_of_theirs = insert(EstablishmentRecord).values(
        id=new_id(),
        organization_id=mine.organization,
        company_id=theirs.id,
        code="SUC9",
        name="X",
        time_zone="America/Santo_Domingo",
        created_at=NOW,
    )
    attempts: tuple[tuple[Scope, Insert], ...] = (
        (
            OrganizationScope(mine.organization, None),
            member_of_theirs,
        ),  # row-level security lets each of these through
        (Tenant(mine.organization, theirs.id, None), establishment_of_theirs),
    )
    for scope, statement in attempts:
        with pytest.raises(IntegrityError) as caught:
            await write(module_database, scope, statement)
        assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


async def with_the_policies_showing[T](
    module_database: BoundDatabase,
    scope: Scope,
    shown: Tenant,
    look: Callable[[IdentityUnit, AsyncConnection], Awaitable[T]],
) -> T:
    """What ``look`` sees in a unit of ``scope`` whose row-level security shows ``shown``'s rows instead.

    What a policy dropped by mistake (or a setting gone wrong) would leave: the repositories, bound to the
    unit's own scope, are the only filter between the unit and another tenant's rows.
    """
    async with module_database.unit(scope) as unit:
        await unit.connection.execute(
            SHOWING, {"organization": str(shown.organization), "company": str(shown.company)}
        )
        return await look(bind(unit), unit.connection)


async def a_company_with_an_establishment(module_database: BoundDatabase) -> tuple[Company, Establishment]:
    company = await create(module_database)
    return company, await open_in(module_database, company, "SUC1")


async def test_the_repositories_keep_to_the_unit_s_organization_without_row_level_security(
    module_database: BoundDatabase,
) -> None:
    company, santiago = await a_company_with_an_establishment(module_database)

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
            await unit.establishments.get(company.id, santiago.id),
            await unit.establishments.by_code(company.id, MAIN_CODE),
            (await unit.establishments.page(company.id, PageRequest())).items,
        ]

    seen = await with_the_policies_showing(
        module_database, OrganizationScope(STRANGER.organization, None), company.tenant, look
    )
    assert seen == [1, None, None, None, (), None, 0, (), None, None, ()]


async def test_a_unit_refuses_to_write_a_row_of_another_tenant(module_database: BoundDatabase) -> None:
    """Row-level security would refuse each of these too: the repositories do not count on it."""
    company = await create(module_database)
    establishment = Establishment.open(
        company, code=parse_code("SUC1"), name=parse_name("S"), time_zone=SANTO_DOMINGO, now=NOW
    )
    owner, _ = found(company, FOUNDER, now=NOW)
    renamed, _ = company.rename(parse_legal_name("Duarte S.A."), now=NOW)
    of_another_organization: tuple[Callable[[IdentityUnit], Awaitable[object]], ...] = (
        lambda unit: unit.organizations.add(Organization.of(FOUNDER, now=NOW)),
        lambda unit: unit.companies.add(company),
        lambda unit: unit.companies.update(renamed, version=company.version),
        lambda unit: unit.establishments.add(establishment),
        lambda unit: unit.memberships.put(owner),
    )
    attempts = tuple(
        (OrganizationScope(STRANGER.organization, None), write) for write in of_another_organization
    )
    for scope, write_elsewhere in attempts:
        with pytest.raises(ForeignTenantError) as caught:
            await in_unit(module_database, scope, write_elsewhere)
        assert caught.value.code == "foreign_tenant"
    kept = await in_unit(module_database, company.tenant, lambda unit: queries.company(unit, company.id))
    assert kept == company


async def test_a_unit_s_time_is_its_transaction_s_for_what_it_records_and_announces(
    module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with module_database.unit(OrganizationScope(FOUNDER.organization, None)) as transaction:
        unit = bind(transaction)
        company = await commands.create_company(
            unit, FOUNDER, profile=profile(), legal_name=parse_legal_name("Duarte"), time_zone=SANTO_DOMINGO
        )
        began = MOMENT.validate_python(
            (await transaction.connection.execute(text("SELECT now()"))).scalar_one()
        )
    assert (unit.now, company.created_at, company.updated_at) == (began, began, began)
    cursor = await admin.execute("SELECT occurred_at FROM identity.outbox ORDER BY id")
    assert [row[0] for row in await cursor.fetchall()] == [began, began]


async def test_identity_s_state_is_never_read_without_a_scope(module_database: BoundDatabase) -> None:
    async with module_database.unit(None) as transaction:
        with pytest.raises(UnscopedUnitError) as caught:
            bind(transaction)
    assert caught.value.code == "unscoped_unit"


# ── Memberships ─────────────────────────────────────────────────────────────────


async def test_roles_are_granted_counted_listed_revoked_and_granted_again(
    module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(module_database)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            module_database,
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
        module_database, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest(size=1))
    )
    assert listed.next_cursor is not None
    assert await in_unit(module_database, tenant, lambda unit: unit.memberships.owners(company.id)) == 1
    await in_unit(
        module_database,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    revoked = await in_unit(
        module_database, tenant, lambda unit: unit.memberships.get(company.id, MEMBER.subject)
    )
    assert revoked is not None
    assert (revoked.active, revoked.version, revoked.changed_by) == (False, 3, OWNER)
    assert await MembershipPermissions(module_database).permissions(MEMBER, company.id) == frozenset()
    remaining = await in_unit(
        module_database, tenant, lambda unit: queries.memberships(unit, company.id, PageRequest())
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
    module_database: BoundDatabase, migrated: WorkerDatabase
) -> None:
    company = await create(module_database)
    tenant = company.tenant
    await in_unit(
        module_database,
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

    winner = asyncio.create_task(in_unit(module_database, tenant, first))
    await holding.wait()
    loser = asyncio.create_task(in_unit(module_database, tenant, second))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the second waits for the company
    assert not loser.done()
    release.set()
    await winner
    with pytest.raises(CompanyNotFoundError):  # its own role went first: it acts in the company no more
        await loser
    assert await in_unit(module_database, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_an_owner_demoted_meanwhile_grants_with_the_role_it_holds_now(
    module_database: BoundDatabase, migrated: WorkerDatabase
) -> None:
    company = await create(module_database)
    tenant = company.tenant
    await in_unit(
        module_database,
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

    demotion = asyncio.create_task(in_unit(module_database, tenant, demote))
    await holding.wait()
    promotion = asyncio.create_task(in_unit(module_database, tenant, promote))
    await eventually(lambda: waits_for_a_lock(app_url(migrated)))  # the promotion waits for the company
    release.set()
    await demotion
    with pytest.raises(OwnersOnlyError):  # checked by the route as an owner, decided as the admin it now is
        await promotion
    assert await in_unit(module_database, tenant, lambda unit: unit.memberships.owners(company.id)) == 1


async def test_permissions_come_from_the_membership_in_the_callers_organization(
    module_database: BoundDatabase,
) -> None:
    company = await create(module_database)
    permissions = MembershipPermissions(module_database)
    assert await permissions.permissions(FOUNDER, company.id) == EVERYTHING
    assert await permissions.permissions(PARTNER, company.id) == frozenset()
    assert await permissions.permissions(FOUNDER, CompanyId(new_id())) == frozenset()


async def test_a_deactivated_company_lets_other_modules_read_only_and_identity_still_revoke(
    module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await create(module_database)
    tenant = company.tenant

    async def grant(role: Role) -> commands.Granted:
        return await in_unit(
            module_database,
            tenant,
            lambda unit: commands.grant_role(
                unit, company.id, subject=MEMBER.subject, role=role, actor=OWNER
            ),
        )

    await grant(Role.ISSUER)
    await in_unit(
        module_database,
        tenant,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=OWNER),
    )
    for_other_services = await in_unit(
        module_database,
        OrganizationScope(FOUNDER.organization, None),
        lambda unit: queries.permissions_of(unit, company.id, OWNER),
    )
    assert for_other_services == READ_ONLY
    permissions = MembershipPermissions(module_database)
    assert await permissions.permissions(FOUNDER, company.id) == EVERYTHING
    with pytest.raises(CompanyInactiveError):  # no role is granted there any more ...
        await grant(Role.VIEWER)
    await in_unit(  # ... but one is still taken away
        module_database,
        tenant,
        lambda unit: commands.revoke_role(unit, company.id, subject=MEMBER.subject, actor=OWNER),
    )
    assert await permissions.permissions(MEMBER, company.id) == frozenset()
    assert (await published(admin))[-2:] == ["identity.company.deactivated", "identity.membership.revoked"]


# ── Pages of establishments ────────────────────────────────────────────────────


async def test_establishments_are_paged_in_id_order_by_cursor(module_database: BoundDatabase) -> None:
    company, santiago = await a_company_with_an_establishment(module_database)
    tenant = company.tenant
    every = sorted(
        [
            await main_of(module_database, company),
            santiago,
            await open_in(module_database, company, "SUC2"),
            await open_in(module_database, company, "SUC3"),
        ],
        key=lambda item: item.id,
    )

    async def listed(request: PageRequest) -> Page[Establishment]:
        return await in_unit(
            module_database, tenant, lambda unit: queries.establishments(unit, company.id, request)
        )

    first = await listed(PageRequest(size=3))
    assert first.next_cursor is not None
    rest = await listed(PageRequest(size=3, after=decode_cursor(first.next_cursor)))
    assert (first.items, rest.items, rest.next_cursor) == (tuple(every[:3]), tuple(every[3:]), None)
    exactly_full = await listed(PageRequest(size=4))
    assert (exactly_full.items, exactly_full.next_cursor) == (tuple(every), None)
