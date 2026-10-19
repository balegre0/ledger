"""identity's API for the other modules (§3.4): Protocols, frozen DTOs and the errors that cross it.

Callers receive an ``IdentityApi`` from the composition root (``apis.get(IDENTITY_API)``, once their
manifest declares the dependency) and never see a domain object or a row. Every method opens a unit of its
own and none may be called while the caller holds one (``UnitOpenError``). A call made for a principal is
limited to the principal's organization; a call the platform makes (a consumer, a job, the receiver) names the
organization it acts for, and only the registry lookup by tax id may name none.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Final, Protocol, TypeIs, final, runtime_checkable
lazy from collections.abc import Sequence

from ledger_identity.domain.errors import CompanyInactiveError
from ledger_kernel.access import CompanyNotFoundError
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.keys import Key
lazy from ledger_kernel.access import Permission, Principal
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.jurisdiction import Jurisdiction
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.tax_ids import TaxId

__all__ = [
    "IDENTITY_API",
    "AnyCall",
    "CompanyInactiveError",
    "CompanyInfo",
    "CompanyNotFoundError",
    "IdentityApi",
]

type AnyCall = Call | SystemCall
"""Who asks: a principal through a request, or the platform itself."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyInfo:
    """A company as other modules see it, at the version it had when it was read."""

    id: CompanyId
    organization: OrganizationId
    jurisdiction: Jurisdiction
    functional_currency: Currency
    tax_ids: Sequence[TaxId]
    """The first is its primary one."""
    legal_name: str
    active: bool
    fiscal_profile_locked: bool
    """Whether its fiscal profile (jurisdiction, functional currency, tax ids) can no longer change (§7.1)."""
    version: int


@runtime_checkable
class IdentityApi(Protocol, metaclass=ABCMeta):
    """What the other modules may ask of identity.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def company(self, call: AnyCall, company: CompanyId) -> CompanyInfo:
        """The company of the call's organization; ``CompanyNotFoundError`` when it has none by that id."""

    @abstractmethod
    async def company_by_tax_id(self, call: AnyCall, tax_id: TaxId) -> CompanyInfo | None:
        """The active company holding ``tax_id``, or ``None``; one deactivated meanwhile is read as inactive.

        A principal's call finds only a company of its own organization (another organization's taxpayer is no
        different from an unknown one); a call the platform makes finds the company of any organization, with
        or without a scope of its own.
        """

    @abstractmethod
    async def permissions(
        self, call: AnyCall, principal: Principal, company: CompanyId
    ) -> frozenset[Permission]:
        """What ``principal`` may do in ``company``.

        Its role's permissions; the reading ones once the company is deactivated; none without an active role.
        """

    @abstractmethod
    async def lock_fiscal_profile(self, call: SystemCall, company: CompanyId) -> CompanyInfo:
        """Lock the company's fiscal profile, before its first numbered document; idempotent.

        ``CompanyNotFoundError`` for a company the call's organization does not have; ``CompanyInactiveError``
        for a deactivated one.
        """


def _is_identity_api(value: object) -> TypeIs[IdentityApi]:
    return isinstance(value, IdentityApi)


IDENTITY_API: Final = Key("identity", _is_identity_api)
"""identity's API under its name: how a module reads it and how identity binds it."""
