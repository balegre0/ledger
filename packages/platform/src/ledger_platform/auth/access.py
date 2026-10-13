"""Authorization: what a principal may do in a company, looked up per request and checked by name.

A ``PermissionSource`` answers with the permissions a principal holds in a company of its organization
(identity's, from its memberships). An empty answer means no role there, and the company is then answered as
not found (``NoCompanyAccessError``): a caller learns nothing of companies it has no part in, its own
organization's or another's. A role without the permission an operation needs is a ``PermissionDeniedError``.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol, final

from ledger_kernel.errors import ForbiddenError, NotFoundError
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Tenant


class PermissionDeniedError(ForbiddenError):
    """The caller holds a role in the company, but not one that allows this."""

    code: ClassVar[str] = "permission_denied"

    permission: Permission

    def __init__(self, permission: Permission) -> None:
        super().__init__(f"this needs the permission {permission.value}")
        self.permission = permission


class NoCompanyAccessError(NotFoundError):
    """The caller holds no role in the company: for it, the company does not exist."""

    code: ClassVar[str] = "company_not_found"

    def __init__(self, company: CompanyId) -> None:
        super().__init__(f"company {company} was not found")


class PermissionSource(Protocol, metaclass=ABCMeta):
    """Where the permissions of a principal in a company come from.

    The method is abstract so that it needs no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def permissions(self, principal: Principal, company: CompanyId) -> frozenset[Permission]:
        """What ``principal`` may do in ``company`` of its organization; empty without a role there."""


@final
@dataclass(frozen=True, slots=True)
class CompanyAccess:
    """A request's caller, the company it acts on (with its organization) and what it may do there.

    ``tenant`` is the scope of a company's transaction (mode-less in this milestone); an organization's is an
    ``OrganizationScope`` of ``principal.organization``.
    """

    principal: Principal
    tenant: Tenant
    permissions: frozenset[Permission]

    def allows(self, permission: Permission) -> bool:
        """Whether the caller holds ``permission`` in the company."""
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Refuse with ``PermissionDeniedError`` unless the caller holds ``permission`` in the company."""
        if permission not in self.permissions:
            raise PermissionDeniedError(permission)
