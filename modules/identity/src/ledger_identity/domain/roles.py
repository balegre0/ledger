"""What each role allows in its company (§7.7): the role table every manifest's permissions are granted by.

The roles are the kernel's (``ledger_kernel.access.Role``). The permissions are the codes manifests declare
(``[[permissions]]``), each with the roles that hold it; this table says the same, and a workspace test holds
it equal to every manifest until the table is generated (v2-M2). The invoice permissions arrive with
invoicing's manifest.
"""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_kernel.access import Role, parse_permission
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
