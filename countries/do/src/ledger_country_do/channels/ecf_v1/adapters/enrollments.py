"""Whether a company is enrolled on the channel: fiscal's answer, through its ``CompanyDirectory`` (D4).

A channel reads no module: fiscal's directory names a company by its primary tax id only while the company
has an enrollment that is not disabled on a channel's environment, in the mode that environment serves. So
the company's tax id is asked first (``company``, of the call's organization), then whether that tax id names
this company there (``receiving_company``); a company of another organization holding the tax id since does
not answer for this one. Both are facades, called outside any unit.
"""

from typing import Final, final, override

from ledger_country_do.channels.ecf_v1.application.ranges import Enrollments
from ledger_country_do.channels.ecf_v1.domain.channel import KEY
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_fiscal_kit.ports import CompanyDirectory
lazy from ledger_kernel.tenancy import Tenant

REASON: Final = "do_ecf:enrollment"
"""What the directory's audit and spans call these questions."""


@final
class DirectoryEnrollments(Enrollments):
    """``Enrollments`` over fiscal's company directory (see the module)."""

    __slots__ = ("_directory",)

    def __init__(self, directory: CompanyDirectory) -> None:
        self._directory = directory

    @override
    async def enrolled(self, tenant: Tenant, environment: Environment) -> bool:
        call = SystemCall(reason=REASON, scope=tenant)
        company = await self._directory.company(call, tenant.company)
        found = await self._directory.receiving_company(
            call, tax_id=company.tax_id, channel=KEY, environment=environment.value
        )
        return found is not None and found.tenant == tenant
