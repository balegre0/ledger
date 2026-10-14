"""Every permission a manifest declares is in identity's role table, granted to exactly its roles (§7.7).

Permissions are an open set, declared by each module and channel in its manifest; identity composes them into
its roles. Until that table is generated from the manifests (v2-M2), this holds the two equal: a permission
declared and not granted, granted to other roles than declared, or granted and declared nowhere fails.
"""

from pathlib import Path
from typing import Final

from ledger_devtools.workspace import Workspace
from ledger_identity.domain.roles import PERMISSIONS
from ledger_kernel.access import Role

ROOT: Final = Path(__file__).resolve().parents[4]


def declared() -> dict[str, frozenset[Role]]:
    """Each permission of every manifest (a module's, a channel's) and the roles it names."""
    return {
        permission.code: frozenset(permission.roles)
        for package in Workspace.load(ROOT).packages
        for permission in (
            *package.manifest.permissions,
            *(permission for channel in package.manifest.channels for permission in channel.permissions),
        )
    }


def granted() -> dict[str, frozenset[Role]]:
    """Each permission of identity's role table and the roles that hold it."""
    every = {permission for permissions in PERMISSIONS.values() for permission in permissions}
    return {
        permission: frozenset(role for role in Role if permission in PERMISSIONS[role])
        for permission in every
    }


def test_every_declared_permission_is_granted_exactly_as_its_manifest_says() -> None:
    assert declared()
    assert granted() == declared()


def test_the_role_table_covers_every_role() -> None:
    assert set(PERMISSIONS) == set(Role)
