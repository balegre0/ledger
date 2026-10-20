"""fiscal-kit's ``CompanyDirectory``, as fiscal provides it to channels (Decision 4): over identity's API.

A channel cannot read ``IdentityApi``; it asks this instead. ``receiving_company`` is the receiver's question:
which company a tax id names on a channel's environment. identity finds the active company that holds the tax
id in any organization (the call is the platform's own); the company is named only while it has an enrollment
that is not disabled on that channel and environment, in the mode the environment serves
(``ChannelDef.mode_of``: an environment serves one mode). ``company`` names a company of the call's
organization in the call's mode; ``permissions`` is identity's answer, for the channel's routes. A company is
named by its legal name and its primary tax id (identity's first).
"""

from typing import final, override

from ledger_fiscal_kit.ports import CompanyDirectory, ReceivingCompany
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import OrganizationScope, Tenant
from ledger_platform.http.problems import current_request_id
from ledger_platform.runtime.calls import Call
from ledger_platform.runtime.contexts import ChannelUnknownError
from ledger_platform.runtime.facade import facade
lazy from ledger_fiscal.application.ports import FiscalUnits
lazy from ledger_fiscal_kit.channels import ChannelRegistry
lazy from ledger_identity.api import CompanyInfo, IdentityApi
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import SystemCall


def _named(company: CompanyInfo, mode: Mode) -> ReceivingCompany:
    return ReceivingCompany(
        organization=company.organization,
        company=company.id,
        mode=mode,
        legal_name=company.legal_name,
        tax_id=company.tax_ids[0],
    )


@final
class FiscalDirectory(CompanyDirectory):
    """Companies as channels need them (see the module)."""

    __slots__ = ("_channels", "_identity", "_units")

    def __init__(self, *, identity: IdentityApi, units: FiscalUnits, channels: ChannelRegistry) -> None:
        self._identity = identity
        self._units = units
        self._channels = channels

    @override
    @facade
    async def receiving_company(
        self, call: SystemCall, *, tax_id: TaxId, channel: str, environment: str
    ) -> ReceivingCompany | None:
        try:
            definition = self._channels.get(channel).definition
        except ChannelUnknownError:
            return None
        if environment not in definition.environments:
            return None
        mode = definition.mode_of(environment)
        company = await self._identity.company_by_tax_id(call, tax_id)
        if company is None or not company.active:  # one deactivated meanwhile is read as inactive
            return None
        async with self._units.unit(Tenant(company.organization, company.id, mode)) as unit:
            enrolled = await unit.enrollments.on(channel, environment)
        return None if enrolled is None else _named(company, mode)

    @override
    @facade
    async def company(self, call: SystemCall, company: CompanyId) -> ReceivingCompany:
        """``company`` of the call's organization, named in the call's mode.

        identity reads the call's organization only: another's company is ``CompanyNotFoundError``.
        """
        scope = call.scope
        if not isinstance(scope, Tenant) or scope.mode is None:
            raise InvalidValueError(field="call.scope", requirement="a company's tenant in a mode")
        return _named(await self._identity.company(call, company), scope.mode)

    @override
    @facade
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        scope = OrganizationScope(principal.organization, None)
        call = Call(principal=principal, scope=scope, request_id=current_request_id() or str(new_id()))
        return await self._identity.permissions(call, principal, company)
