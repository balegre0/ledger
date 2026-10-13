from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from ledger_devtools.workspace import Workspace, WorkspaceError
from ledger_platform.runtime.manifest import PackageKind

DO_TESTING: Final = """
[package]
name = "do-testing"
kind = "package"
summary = "A fake DGII."
owners = ["@o"]

[dependencies]
packages = ["kernel"]
"""
DEVTOOLS: Final = """
[package]
name = "devtools"
kind = "tool"
summary = "Generates and checks."
owners = ["@o"]
"""
BILLING: Final = """
[package]
name = "billing"
kind = "module"
summary = "Bills."
owners = ["@o"]

[dependencies]
modules = ["identity"]

[data]
schema = "billing"

[[events.consumes]]
consumer = "billing.companies"
types = ["identity.company.registered"]
"""

COUNTRY: Final = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@o"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "DGII", short = "DGII", url = "https://dgii.gov.do" }

[[channels]]
key = "do-ecf-v1"
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"
assembly = "ledger_country_do.channels.ecf_v1.assembly"
schema = "do_ecf"
valid_from = 2019-01-01
environments = ["ecf"]
document_kinds = ["invoice"]
directions = ["sales"]
tier = "bronze"
"""

type Writer = Callable[[str, str, tuple[str, ...]], Path]


def test_every_manifest_is_found_and_read(tree: Path) -> None:
    workspace = Workspace.load(tree)
    assert [package.distribution for package in workspace.packages] == [
        "ledger-app-api",
        "ledger-identity",
        "ledger-kernel",
        "ledger-platform",
    ]
    identity = workspace.by_name("identity")
    assert identity.source == Path("modules/identity/src/ledger_identity")
    assert (identity.name, identity.kind, identity.import_name) == (
        "identity",
        PackageKind.MODULE,
        "ledger_identity",
    )
    assert identity.raw["package"] == {
        "name": "identity",
        "kind": "module",
        "summary": "Organizations and companies.",
        "owners": ["@o"],
    }
    assert workspace.schemas() == ("platform", "identity")
    assert [package.name for package in workspace.apps] == ["api"]
    assert [package.name for package in workspace.modules] == ["identity"]
    assert workspace.countries == ()


def test_modules_are_ordered_after_what_they_depend_on_or_consume(tree: Path, write: Writer) -> None:
    write("modules/billing", BILLING, ())
    workspace = Workspace.load(tree)
    assert [package.name for package in workspace.module_order()] == ["identity", "billing"]
    assert workspace.schemas() == ("platform", "identity", "billing")


def test_an_unknown_name_is_a_key_error(tree: Path) -> None:
    with pytest.raises(KeyError, match="ghost"):
        Workspace.load(tree).by_name("ghost")


def test_a_tree_without_manifests_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match=r"no manifest\.toml") as caught:
        Workspace.load(tmp_path)
    assert caught.value.code == "workspace_unreadable"


def test_an_invalid_manifest_is_reported_with_its_path(tree: Path) -> None:
    (tree / "apps/api/manifest.toml").write_text("[package]\nname = 1\n", encoding="utf-8")
    (tree / "modules/identity/manifest.toml").write_text("not = [toml", encoding="utf-8")
    with pytest.raises(WorkspaceError) as caught:
        Workspace.load(tree)
    assert str(caught.value).startswith("apps/api/manifest.toml: manifest: package.name: ")
    assert "; modules/identity/manifest.toml: manifest: line 1: not TOML" in str(caught.value)


def test_dev_only_distributions_are_not_production(tree: Path, write: Writer) -> None:
    write("tools/devtools", DEVTOOLS, ())
    write("countries/do/testing", DO_TESTING, ())
    workspace = Workspace.load(tree)
    assert workspace.by_name("identity").is_production
    assert not workspace.by_name("devtools").is_production
    assert workspace.by_name("devtools").nested_country is None
    nested = workspace.by_name("do-testing")
    assert (nested.nested_country, nested.distribution, nested.import_name) == (
        "do",
        "ledger-country-do-testing",
        "ledger_country_do_testing",
    )
    assert nested.source == Path("countries/do/testing/src/ledger_country_do_testing")
    assert not nested.is_production


def test_channels_own_schemas_after_the_modules(tree: Path, write: Writer) -> None:
    write("modules/billing", BILLING, ())
    write("countries/do", COUNTRY, ())
    assert Workspace.load(tree).schemas() == ("platform", "identity", "billing", "do_ecf")


def test_children_are_the_layers_on_disk(tree: Path) -> None:
    workspace = Workspace.load(tree)
    assert workspace.children(workspace.by_name("identity")) == ("adapters", "application", "domain")
    (tree / "apps/api/src/ledger_app_api/__init__.py").unlink()
    (tree / "apps/api/src/ledger_app_api").rmdir()
    assert workspace.children(workspace.by_name("api")) == ()
