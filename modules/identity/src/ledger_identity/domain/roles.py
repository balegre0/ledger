"""What each role allows in its company (§7.7): the role table every manifest's permissions are granted by.

The roles are the kernel's (``ledger_kernel.access.Role``). The permissions are the codes manifests declare
(``[[permissions]]``), each with the roles that hold it; this table says the same, and a workspace test holds
it equal to every manifest until the table is generated (v2-M2). The invoice permissions arrive with
invoicing's manifest.
"""

from types import MappingProxyType
from typing import Final
lazy from collections.abc import Collection, Mapping

from ledger_kernel.access import Role, parse_permission
lazy from ledger_kernel.access import Permission

COMPANIES_READ: Final = parse_permission("identity.companies.read")
"""Read the company and its establishments."""
COMPANIES_MANAGE: Final = parse_permission("identity.companies.manage")
"""Change or deactivate the company; open establishments."""
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
"""What identity's roles still allow other modules to do for a deactivated company: read."""
_READ_VERB: Final = ".read"


def only_reads(permissions: Collection[Permission]) -> frozenset[Permission]:
    """Those of ``permissions`` that read: their action, the last part of the code, is ``read``.

    Decided by the verb, not by a list, so that the permissions every module declares are covered: a
    deactivated company keeps its data readable everywhere and issues nothing anywhere (§7.7).
    """
    return frozenset(permission for permission in permissions if permission.endswith(_READ_VERB))


def permissions_of(role: Role) -> frozenset[Permission]:
    """What ``role`` allows in its company."""
    return PERMISSIONS[role]
