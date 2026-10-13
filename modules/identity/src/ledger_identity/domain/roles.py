"""Roles granted per company, and what each allows (§8)."""

from enum import StrEnum
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_kernel.access import Permission


class Role(StrEnum):
    """A principal's role in one company."""

    OWNER = "owner"
    """Everything, including who else owns the company."""
    ADMIN = "admin"
    """Everything but the owner role."""
    ACCOUNTANT = "accountant"
    """Reads everything, issues documents."""
    ISSUER = "issuer"
    """Issues documents."""
    VIEWER = "viewer"
    """Reads."""


_EVERYTHING: Final = frozenset(Permission)
PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = MappingProxyType(
    {
        Role.OWNER: _EVERYTHING,
        Role.ADMIN: _EVERYTHING - {Permission.OWNERSHIP_MANAGE},
        Role.ACCOUNTANT: frozenset(
            {
                Permission.COMPANY_READ,
                Permission.MEMBERSHIP_READ,
                Permission.INVOICE_READ,
                Permission.INVOICE_ISSUE,
            }
        ),
        Role.ISSUER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ, Permission.INVOICE_ISSUE}),
        Role.VIEWER: frozenset({Permission.COMPANY_READ, Permission.INVOICE_READ}),
    }
)


READ_ONLY: Final = frozenset({Permission.COMPANY_READ, Permission.MEMBERSHIP_READ, Permission.INVOICE_READ})
"""What a role still allows other services to do for a deactivated company: read."""


def permissions_of(role: Role) -> frozenset[Permission]:
    """What ``role`` allows in its company."""
    return PERMISSIONS[role]
