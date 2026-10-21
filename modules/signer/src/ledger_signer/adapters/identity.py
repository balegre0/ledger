"""What a caller may do in a company, as identity says: the authorization of signer's routes (§7.7).

Asked of ``IdentityApi.permissions`` on every request, no cache: the permissions of the caller's active role,
only the reading ones once the company is deactivated, none without a role (the company is then not found).
The call names the caller's organization and no mode: a role is the company's, in both modes. Only ``api``
asks it: the signer's own role reads no other module's API.
"""

from typing import final, override

from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import OrganizationScope
from ledger_platform.auth import PermissionSource
from ledger_platform.http.problems import current_request_id
from ledger_platform.runtime.calls import Call
lazy from ledger_identity.api import IdentityApi
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId


@final
class IdentityPermissions(PermissionSource):
    """The permissions identity's memberships give, through its API."""

    __slots__ = ("_identity",)

    def __init__(self, identity: IdentityApi) -> None:
        self._identity = identity

    @override
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        scope = OrganizationScope(principal.organization, None)
        call = Call(principal=principal, scope=scope, request_id=current_request_id() or str(new_id()))
        return await self._identity.permissions(call, principal, company)
