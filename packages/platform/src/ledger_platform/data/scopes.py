"""The scope a queue row's envelope columns name (outbox, inbox and jobs rows carry them outside RLS)."""

from typing import Final
lazy from uuid import UUID

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
lazy from ledger_kernel.tenancy import Scope

_MODES: Final = frozenset(mode.value for mode in Mode)


def scope_of(organization: UUID | None, company: UUID | None, mode: str | None) -> Scope | None:
    """``Tenant`` with a company, ``OrganizationScope`` without one, ``None`` without an organization.

    A mode other than ``test``, ``live`` or none is refused, whatever the other columns hold: a corrupt row.
    """
    if mode is not None and mode not in _MODES:
        raise InvalidValueError(field="row.mode", requirement="test, live or null")
    parsed = None if mode is None else Mode(mode)
    if organization is None:
        return None
    if company is None:
        return OrganizationScope(OrganizationId(organization), parsed)
    return Tenant(OrganizationId(organization), CompanyId(company), parsed)
