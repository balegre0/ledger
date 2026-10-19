"""What each role allows in its company (§7.7): the role table, generated from every manifest's permissions.

The roles are the kernel's (``ledger_kernel.access.Role``). The permissions are the codes manifests declare
(``[[permissions]]``), each with the roles that hold it: ``ledger-devtools sync`` writes the table from them
(``_role_grants.ROLE_GRANTS``) and ``check`` fails while it is stale, so a module's new permission reaches
identity's roles with no edit here. identity names only its own codes, the ones it checks itself.
"""

from typing import Final
lazy from collections.abc import Collection, Mapping

from ledger_identity.domain._role_grants import ROLE_GRANTS
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
PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = ROLE_GRANTS
"""Each built-in role and what it allows: the generated table."""
EVERYTHING: Final = PERMISSIONS[Role.OWNER]
"""Every permission the manifests declare: the owner holds them all (§7.7)."""
_READ_VERB: Final = ".read"


def only_reads(permissions: Collection[Permission]) -> frozenset[Permission]:
    """Those of ``permissions`` that read: their action, the last part of the code, is ``read``.

    Decided by the verb, not by a list, so that the permissions every module declares are covered: a
    deactivated company keeps its data readable everywhere and issues nothing anywhere (§7.7). The naming
    convention this rests on, which every manifest keeps: a code is ``<module>.<resource>.<action>``, and a
    permission that only reads has the action ``read`` and nothing that changes anything does (the manifest
    model refuses any other action than ``read``, ``create``, ``issue``, ``void``, ``manage`` and ``own``).
    """
    return frozenset(permission for permission in permissions if permission.endswith(_READ_VERB))


def permissions_of(role: Role) -> frozenset[Permission]:
    """What ``role`` allows in its company."""
    return PERMISSIONS[role]
