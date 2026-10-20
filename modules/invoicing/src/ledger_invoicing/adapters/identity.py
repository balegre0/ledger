"""identity as invoicing asks it: ``IdentityLookup`` over ``IdentityApi`` (§3.4).

Each answer is identity's, as it has it when asked, outside any unit of invoicing's (rule 13: the API refuses
a call made while one is open). An establishment comes back as what a document keeps of it: its code and the
time zone of its days.
"""

from typing import final, override

from ledger_invoicing.application.ports import IdentityLookup
from ledger_invoicing.domain.pricing import EstablishmentRef
lazy from ledger_identity.api import CompanyInfo, IdentityApi
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.runtime.calls import Call


@final
class IdentityApiLookup(IdentityLookup):
    """The company and its establishments, through identity's API."""

    __slots__ = ("_identity",)

    def __init__(self, identity: IdentityApi) -> None:
        self._identity = identity

    @override
    async def company(self, call: Call, company: CompanyId) -> CompanyInfo:
        return await self._identity.company(call, company)

    @override
    async def establishment(
        self, call: Call, company: CompanyId, code: str | None
    ) -> EstablishmentRef | None:
        found = await self._identity.establishment(call, company, code)
        return None if found is None else EstablishmentRef(code=found.code, time_zone=found.time_zone)
