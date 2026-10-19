"""identity's role table, generated from every manifest's permissions (§7.7)."""

from pathlib import Path
lazy from collections.abc import Callable

from ledger_devtools.generate import targets
from ledger_devtools.roles import ROLE_GRANTS_HEADER, role_grants, role_grants_path, role_grants_text
from ledger_devtools.workspace import Workspace
from ledger_kernel.access import Role

type Writer = Callable[[str, str, tuple[str, ...]], Path]
type Editor = Callable[[str, str, str], None]

PERMISSIONS = """
[[permissions]]
code = "identity.companies.read"
summary = "Read."
roles = ["owner", "viewer"]

[[permissions]]
code = "identity.ownership.manage"
summary = "Own."
roles = ["owner"]
"""
BILLING = """
[package]
name = "billing"
kind = "module"
summary = "Bills."
owners = ["@o"]

[dependencies]
modules = ["identity"]

[[permissions]]
code = "billing.bills.issue"
summary = "Issue."
roles = ["owner", "issuer"]
"""


def test_every_role_holds_what_the_manifests_grant_it_in_the_kernel_s_order(
    tree: Path, edit: Editor, write: Writer
) -> None:
    edit("modules/identity", "[data]", PERMISSIONS.strip() + "\n\n[data]")
    write("modules/billing", BILLING, ())
    workspace = Workspace.load(tree)
    assert role_grants(workspace) == {
        Role.OWNER: ("billing.bills.issue", "identity.companies.read", "identity.ownership.manage"),
        Role.ADMIN: (),
        Role.ACCOUNTANT: (),
        Role.ISSUER: ("billing.bills.issue",),
        Role.VIEWER: ("identity.companies.read",),
    }
    assert role_grants_text(workspace) == EXPECTED


EXPECTED = (
    ROLE_GRANTS_HEADER
    + """
Edit the manifests, then run `uv run ledger-devtools sync`.
\"\"\"

from types import MappingProxyType
from typing import Final

from ledger_kernel.access import Permission, Role

ROLE_GRANTS: Final = MappingProxyType(
    {
        Role.OWNER: frozenset(
            {
                Permission("billing.bills.issue"),
                Permission("identity.companies.read"),
                Permission("identity.ownership.manage"),
            }
        ),
        Role.ADMIN: frozenset(),
        Role.ACCOUNTANT: frozenset(),
        Role.ISSUER: frozenset(
            {
                Permission("billing.bills.issue"),
            }
        ),
        Role.VIEWER: frozenset(
            {
                Permission("identity.companies.read"),
            }
        ),
    }
)
\"\"\"What each built-in role allows in its company: every permission a manifest grants it (§7.7).\"\"\"
"""
)
"""The table for identity's and billing's permissions, as ruff formats it (the gate checks the real one)."""


def test_the_table_is_written_into_identity_s_domain_and_only_where_identity_is(tree: Path) -> None:
    workspace = Workspace.load(tree)
    path = Path("modules/identity/src/ledger_identity/domain/_role_grants.py")
    assert role_grants_path(workspace) == path
    assert targets(workspace, document=dict)[path] == role_grants_text(workspace)
    (tree / "modules/identity/manifest.toml").unlink()
    without = Workspace.load(tree)
    assert role_grants_path(without) is None
    assert not [written for written in targets(without, document=dict) if written.name == "_role_grants.py"]
