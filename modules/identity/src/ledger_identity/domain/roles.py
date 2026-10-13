"""Roles granted per company, and what each allows (§7.7).

The permissions are the codes identity's manifest declares (``[[permissions]]``), each with the roles that
hold it; this table says the same, and a test holds the two together until the table is generated (v2-M2).
The invoice permissions arrive with invoicing's manifest.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_kernel.access import parse_permission
lazy from ledger_kernel.access import Permission

COMPANIES_READ: Final = parse_permission("identity.companies.read")
"""Read the company, its branches and its emission points."""
COMPANIES_MANAGE: Final = parse_permission("identity.companies.manage")
"""Change or deactivate the company; open branches and emission points."""
MEMBERSHIPS_READ: Final = parse_permission("identity.memberships.read")
"""See who holds which role in the company."""
MEMBERSHIPS_MANAGE: Final = parse_permission("identity.memberships.manage")
"""Grant and revoke every role but the owner's."""
OWNERSHIP_MANAGE: Final = parse_permission("identity.ownership.manage")
"""Grant and revoke the owner role."""
EVERYTHING: Final = frozenset(
    {COMPANIES_READ, COMPANIES_MANAGE, MEMBERSHIPS_READ, MEMBERSHIPS_MANAGE, OWNERSHIP_MANAGE}
)
"""Every permission identity declares."""


class Role(StrEnum):
    """A principal's role in one company."""

    OWNER = "owner"
    """Everything, including who else owns the company."""
    ADMIN = "admin"
    """Everything but the owner role."""
    ACCOUNTANT = "accountant"
    """Reads the company and its memberships (and, with invoicing, its documents)."""
    ISSUER = "issuer"
    """Reads the company (and, with invoicing, issues documents)."""
    VIEWER = "viewer"
    """Reads."""


PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = MappingProxyType(
    {
        Role.OWNER: EVERYTHING,
        Role.ADMIN: EVERYTHING - {OWNERSHIP_MANAGE},
        Role.ACCOUNTANT: frozenset({COMPANIES_READ, MEMBERSHIPS_READ}),
        Role.ISSUER: frozenset({COMPANIES_READ}),
        Role.VIEWER: frozenset({COMPANIES_READ}),
    }
)


READ_ONLY: Final = frozenset({COMPANIES_READ, MEMBERSHIPS_READ})
"""What a role still allows other modules to do for a deactivated company: read."""


def permissions_of(role: Role) -> frozenset[Permission]:
    """What ``role`` allows in its company."""
    return PERMISSIONS[role]
