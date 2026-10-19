"""identity's API for other modules (§3.4), for real: the facade over PostgreSQL, called as they call it.

Every method opens a unit of its own; none may be called while a unit is open. A principal's call is limited
to its organization; a system call (a consumer, a job, the receiver) names the one it acts for or, for the
registry, none.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable
lazy from contextlib import AbstractAsyncContextManager

import pytest
from pydantic import TypeAdapter
lazy import psycopg

from ledger_identity.adapters.postgres.unit import PgUnits, bind
from ledger_identity.api import IDENTITY_API, CompanyInfo
from ledger_identity.application import commands
from ledger_identity.application.facade import IdentityFacade
from ledger_identity.application.ports import IdentityUnits
from ledger_identity.domain.errors import CompanyInactiveError
from ledger_identity.domain.organization import ORGANIZATION_ADMIN
from ledger_identity.domain.roles import EVERYTHING, READ_ONLY
from ledger_identity.domain.text import parse_legal_name, parse_time_zone
from ledger_kernel.access import CompanyNotFoundError, Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.auth.providers.zitadel import organization_of
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.facade import UnitOpenError, is_facade, require_facades
lazy from ledger_identity.api import IdentityApi
lazy from ledger_identity.application.ports import IdentityUnit, TaxRegistry
lazy from ledger_identity.domain.company import Company, FiscalProfile
lazy from ledger_kernel.tenancy import Scope
lazy from ledger_platform.data.bound import BoundDatabase

NOW: Final = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FOUNDER: Final = Principal(
    subject=SubjectId("2001"),
    organization=organization_of("1001"),
    provider_organization="1001",
    roles=frozenset({ORGANIZATION_ADMIN}),
    kind=PrincipalKind.HUMAN,
)
STRANGER: Final = Principal(
    subject=SubjectId("2001"),
    organization=organization_of("1002"),
    provider_organization="1002",
    roles=frozenset({ORGANIZATION_ADMIN}),
    kind=PrincipalKind.HUMAN,
)
MEMBER: Final = Principal(
    subject=SubjectId("3001"),
    organization=FOUNDER.organization,
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
CEDULA: Final = TaxId(TaxIdScheme("do.cedula"), "00114672381")
THEIRS: Final = TaxId(TaxIdScheme("do.rnc"), "401500022")
SANTO_DOMINGO: Final = parse_time_zone("America/Santo_Domingo")


def anything(version: int) -> bool:
    del version
    return True


TYPES: Final = TypeAdapter(list[tuple[str]])


def called_by(principal: Principal) -> Call:
    return Call(
        principal=principal, scope=OrganizationScope(principal.organization, None), request_id="request-1"
    )


def by_the_platform(organization: Principal | None = FOUNDER) -> SystemCall:
    scope = None if organization is None else OrganizationScope(organization.organization, None)
    return SystemCall(reason="test", scope=scope)


async def in_unit[T](
    module_database: BoundDatabase, principal: Principal, work: Callable[[IdentityUnit], Awaitable[T]]
) -> T:
    async with module_database.unit(OrganizationScope(principal.organization, None)) as unit:
        return await work(bind(unit))


def profile(*tax_ids: TaxId) -> FiscalProfile:
    return FiscalProfile(
        jurisdiction=parse_jurisdiction("DO"), functional_currency=Currency.of("DOP"), tax_ids=tax_ids
    )


async def register(module_database: BoundDatabase, founder: Principal = FOUNDER, *tax_ids: TaxId) -> Company:
    return await in_unit(
        module_database,
        founder,
        lambda unit: commands.create_company(
            unit,
            founder,
            commands.Registration(
                profile=profile(*(tax_ids or (RNC,))),
                legal_name=parse_legal_name("Duarte"),
                time_zone=SANTO_DOMINGO,
                api_version=date(2026, 10, 13),
            ),
        ),
    )


@pytest.fixture
def api(module_database: BoundDatabase) -> IdentityApi:
    return IDENTITY_API.take(IdentityFacade(PgUnits(module_database)))


def shown(company: Company) -> CompanyInfo:
    """What the API tells of ``company``."""
    return CompanyInfo(
        id=company.id,
        organization=company.organization,
        jurisdiction=company.profile.jurisdiction,
        functional_currency=company.profile.functional_currency,
        tax_ids=company.profile.tax_ids,
        legal_name=company.legal_name,
        active=True,
        fiscal_profile_locked=company.fiscal_profile_locked_at is not None,
        version=company.version,
    )


# ── A company by id ─────────────────────────────────────────────────────────────


async def test_a_company_is_read_by_a_caller_of_its_organization(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    assert await api.company(called_by(MEMBER), company.id) == shown(company)
    assert await api.company(by_the_platform(), company.id) == shown(company)


async def test_a_company_of_another_organization_is_not_found(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    for call in (called_by(STRANGER), by_the_platform(STRANGER)):
        with pytest.raises(CompanyNotFoundError):
            await api.company(call, company.id)
    with pytest.raises(CompanyNotFoundError):
        await api.company(called_by(FOUNDER), CompanyId(new_id()))


async def test_a_system_call_of_no_organization_cannot_read_a_company(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    with pytest.raises(InvalidValueError) as caught:
        await api.company(by_the_platform(None), company.id)
    assert caught.value.code == "invalid_value"


async def test_a_deactivated_company_is_read_as_inactive(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    await in_unit(
        module_database,
        FOUNDER,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=FOUNDER.subject),
    )
    read = await api.company(called_by(FOUNDER), company.id)
    assert (read.active, read.version) == (False, company.version + 1)


# ── A company by tax id ─────────────────────────────────────────────────────────


async def test_the_platform_finds_the_active_company_of_any_organization_by_any_of_its_tax_ids(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    mine = await register(module_database, FOUNDER, RNC, CEDULA)
    theirs = await register(module_database, STRANGER, THEIRS)
    receiver = by_the_platform(None)  # the receiver knows a taxpayer, not whose company it is
    assert await api.company_by_tax_id(receiver, CEDULA) == shown(mine)
    assert await api.company_by_tax_id(receiver, RNC) == shown(mine)
    assert await api.company_by_tax_id(receiver, THEIRS) == shown(theirs)
    assert await api.company_by_tax_id(receiver, TaxId(TaxIdScheme("do.rnc"), "130000012")) is None


async def test_a_principal_finds_the_company_of_its_own_organization_by_tax_id_and_no_other(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    mine = await register(module_database)
    theirs = await register(module_database, STRANGER, THEIRS)
    assert await api.company_by_tax_id(called_by(FOUNDER), RNC) == shown(mine)
    # another organization's taxpayer is no different from an unknown one: it tells nothing
    assert await api.company_by_tax_id(called_by(FOUNDER), THEIRS) is None
    assert await api.company_by_tax_id(called_by(STRANGER), THEIRS) == shown(theirs)


@final
class DeactivatedMeanwhile(IdentityUnits):
    """Units that deactivate a company just before the facade first reads it: what a taxpayer found in the
    registry can become, between the facade's two units."""

    __slots__ = ("_company", "_database", "_pending", "_units")

    def __init__(self, database: BoundDatabase, company: Company) -> None:
        self._database = database
        self._units = PgUnits(database)
        self._company = company
        self._pending = True

    @override
    @asynccontextmanager
    async def unit(self, scope: Scope) -> AsyncGenerator[IdentityUnit]:
        if self._pending:
            self._pending = False
            await in_unit(
                self._database,
                FOUNDER,
                lambda unit: commands.deactivate_company(
                    unit, self._company.id, admits=anything, actor=FOUNDER.subject
                ),
            )
        async with self._units.unit(scope) as unit:
            yield unit

    @override
    def registry(self) -> AbstractAsyncContextManager[TaxRegistry]:
        return self._units.registry()


async def test_a_company_deactivated_between_the_registry_and_its_read_is_read_as_inactive(
    module_database: BoundDatabase,
) -> None:
    company = await register(module_database)
    racing = IdentityFacade(DeactivatedMeanwhile(module_database, company))
    found = await racing.company_by_tax_id(by_the_platform(None), RNC)
    assert found is not None
    assert (found.id, found.active) == (company.id, False)


async def test_a_deactivated_company_is_not_found_by_its_tax_id(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    await in_unit(
        module_database,
        FOUNDER,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=FOUNDER.subject),
    )
    assert await api.company_by_tax_id(by_the_platform(None), RNC) is None


# ── Permissions ─────────────────────────────────────────────────────────────────


async def test_a_principal_s_permissions_are_its_role_s_and_the_reading_ones_once_deactivated(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    assert await api.permissions(called_by(FOUNDER), FOUNDER, company.id) == EVERYTHING
    assert await api.permissions(called_by(FOUNDER), MEMBER, company.id) == frozenset()  # no role
    await in_unit(
        module_database,
        FOUNDER,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=FOUNDER.subject),
    )
    assert await api.permissions(called_by(FOUNDER), FOUNDER, company.id) == READ_ONLY
    assert await api.permissions(called_by(STRANGER), STRANGER, company.id) == frozenset()


# ── Locking the fiscal profile ──────────────────────────────────────────────────


async def test_the_fiscal_profile_is_locked_once_and_the_lock_is_announced_and_audited_as_the_platform(
    api: IdentityApi, module_database: BoundDatabase, admin: psycopg.AsyncConnection
) -> None:
    company = await register(module_database)
    call = SystemCall(
        reason="fiscal.first-number", scope=Tenant(company.organization, company.id, None), call_id="call-1"
    )
    locked = await api.lock_fiscal_profile(call, company.id)
    assert (locked.fiscal_profile_locked, locked.version) == (True, company.version + 1)
    again = await api.lock_fiscal_profile(call, company.id)
    assert again == locked  # a retry changes nothing
    read = await api.company(called_by(FOUNDER), company.id)
    assert read == locked
    events = await admin.execute(
        "SELECT type FROM identity.outbox WHERE subject = %s ORDER BY id", (company.id,)
    )
    assert [kind for (kind,) in TYPES.validate_python(await events.fetchall())] == [
        "identity.company.registered",
        "identity.company.updated",
    ]
    audit = await admin.execute(
        "SELECT actor, call_id FROM identity.audit_events WHERE action = 'company.lock_fiscal_profile'"
    )
    assert await audit.fetchall() == [("system:fiscal.first-number", "call-1")]


async def test_a_locked_profile_is_kept_by_the_company_it_belongs_to(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    await api.lock_fiscal_profile(by_the_platform(), company.id)
    stored = await in_unit(module_database, FOUNDER, lambda unit: unit.companies.get(company.id))
    assert stored is not None
    assert stored.fiscal_profile_locked_at is not None
    assert replace(stored, fiscal_profile_locked_at=None).profile == company.profile


async def test_a_deactivated_company_s_profile_is_not_locked(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    await in_unit(
        module_database,
        FOUNDER,
        lambda unit: commands.deactivate_company(unit, company.id, admits=anything, actor=FOUNDER.subject),
    )
    with pytest.raises(CompanyInactiveError):
        await api.lock_fiscal_profile(by_the_platform(), company.id)


async def test_the_profile_of_a_company_of_another_organization_is_not_locked(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    with pytest.raises(CompanyNotFoundError):
        await api.lock_fiscal_profile(by_the_platform(STRANGER), company.id)


async def test_a_system_call_of_no_organization_cannot_lock_a_profile(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    with pytest.raises(InvalidValueError):
        await api.lock_fiscal_profile(by_the_platform(None), company.id)


# ── The guard ───────────────────────────────────────────────────────────────────


async def test_no_method_is_called_while_a_unit_is_open(
    api: IdentityApi, module_database: BoundDatabase
) -> None:
    company = await register(module_database)
    calls: tuple[Callable[[], Awaitable[object]], ...] = (
        lambda: api.company(called_by(FOUNDER), company.id),
        lambda: api.company_by_tax_id(called_by(FOUNDER), RNC),
        lambda: api.permissions(called_by(FOUNDER), FOUNDER, company.id),
        lambda: api.lock_fiscal_profile(by_the_platform(), company.id),
    )
    for call in calls:
        async with module_database.unit(OrganizationScope(company.organization, None)):
            with pytest.raises(UnitOpenError):
                await call()


def test_the_facade_has_nothing_unguarded_for_other_modules_to_call(api: IdentityApi) -> None:
    require_facades(api)
    assert all(
        is_facade(method)
        for method in (
            IdentityFacade.company,
            IdentityFacade.company_by_tax_id,
            IdentityFacade.permissions,
            IdentityFacade.lock_fiscal_profile,
        )
    )
