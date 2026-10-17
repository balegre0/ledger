"""identity's API for the other modules (``api.IdentityApi``), over its use cases.

Each method opens the unit its use case needs, in the organization the call names, and gives back data
(``CompanyInfo``), never a domain object. ``@facade`` refuses a call made while the caller holds a unit.
"""

from typing import final, override

from ledger_identity.api import CompanyInfo, IdentityApi
from ledger_identity.application import commands, queries
from ledger_identity.domain.company import CompanyStatus
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call, SystemCall, acting
from ledger_platform.runtime.facade import facade
lazy from ledger_identity.api import AnyCall
lazy from ledger_identity.application.ports import IdentityUnits
lazy from ledger_identity.domain.company import Company
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tax_ids import TaxId


def info(company: Company) -> CompanyInfo:
    """What other modules are told of ``company``."""
    return CompanyInfo(
        id=company.id,
        organization=company.organization,
        jurisdiction=company.profile.jurisdiction,
        functional_currency=company.profile.functional_currency,
        tax_ids=company.profile.tax_ids,
        legal_name=company.legal_name,
        active=company.status is CompanyStatus.ACTIVE,
        fiscal_profile_locked=company.fiscal_profile_locked_at is not None,
        version=company.version,
    )


def organization_of(call: AnyCall) -> OrganizationId:
    """The organization ``call`` acts for; a system call that names none is a defect of its caller."""
    match call:
        case Call(scope=scope) | SystemCall(scope=scope) if scope is not None:
            return scope.organization
        case _:
            raise InvalidValueError(field="call.scope", requirement="an organization or company scope")


@final
class IdentityFacade(IdentityApi):
    """The API, as identity implements it."""

    __slots__ = ("_units",)

    def __init__(self, units: IdentityUnits) -> None:
        self._units = units

    @override
    @facade
    async def company(self, call: AnyCall, company: CompanyId) -> CompanyInfo:
        async with self._units.unit(OrganizationScope(organization_of(call), None)) as unit:
            return info(await queries.company(unit, company))

    @override
    @facade
    async def company_by_tax_id(self, call: AnyCall, tax_id: TaxId) -> CompanyInfo | None:
        async with self._units.registry() as registry:
            holder = await registry.holder_of(tax_id)
        if holder is None:
            return None
        if isinstance(call, Call) and holder.organization != organization_of(call):
            return None  # another organization's taxpayer is told no more than an unknown one
        async with self._units.unit(OrganizationScope(holder.organization, None)) as unit:
            return info(await queries.company(unit, holder.company))  # the registry's key is a company's

    @override
    @facade
    async def permissions(
        self, call: AnyCall, principal: Principal, company: CompanyId
    ) -> frozenset[Permission]:
        del call
        async with self._units.unit(OrganizationScope(principal.organization, None)) as unit:
            return await queries.permissions_of(unit, company, principal.subject)

    @override
    @facade
    async def lock_fiscal_profile(self, call: SystemCall, company: CompanyId) -> CompanyInfo:
        scope = Tenant(organization_of(call), company, None)
        with acting(call):
            async with self._units.unit(scope) as unit:
                return info(await commands.lock_fiscal_profile(unit, company))
