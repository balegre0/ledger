from collections.abc import Callable
from pathlib import Path
from typing import Final

from ledger_devtools.checks import Problem, check
from ledger_devtools.repository import repository_problems
from ledger_devtools.workspace import Workspace

type Writer = Callable[[str, str, tuple[str, ...]], Path]
type Editor = Callable[[str, str, str], None]

COUNTRY: Final = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "DGII", short = "DGII", url = "https://dgii.gov.do" }

[[regimes]]
key = "do"
entry_point = "ledger_country_do.regime:REGIME"

[[channels]]
key = "do-ecf-v1"
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"
schema = "do_ecf"
valid_from = 2019-01-01
environments = ["testecf", "certecf", "ecf"]
document_kinds = ["invoice"]
directions = ["sales"]
http_prefixes = ["/v1/do"]
tier = "bronze"

[[channels.ids]]
prefix = "ecf"
resource = "ecf"

[[channels.permissions]]
code = "do_ecf.documents.read"
summary = "Read e-CF documents."

[[channels.events.publishes]]
type = "do_ecf.document.accepted"
version = 1

[[channels.events.consumes]]
consumer = "do_ecf.companies"
types = ["identity.company.registered"]

[[channels.jobs]]
kind = "do_ecf.advance"

[[channels.schedules]]
name = "do_ecf.poll"
every = "1m"
enqueues = "do_ecf.advance"
"""
BILLING: Final = """
[package]
name = "billing"
kind = "module"
summary = "Bills."
owners = ["@o"]

[dependencies]
modules = ["identity"]

[[events.publishes]]
type = "billing.thing.done"
version = 1
"""
NESTED: Final = """
[package]
name = "testing"
kind = "package"
summary = "A fake DGII."
owners = ["@o"]
"""
IDENTITY: Final = "modules/identity"
DEPENDS_ON_DO: Final = """
[dependencies]
packages = ["do"]
"""
PLATFORM: Final = "packages/platform"
COUNTRY_DEPENDENCY: Final = (
    "dependencies.packages names 'do', a country, which only apps and its own testing package may depend on"
)


def messages(root: Path) -> list[str]:
    return sorted(f"{problem.where}: {problem.message}" for problem in check(Workspace.load(root)))


def test_a_coherent_workspace_has_no_problem(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ("regime", "channels"))
    write("countries/do/testing", NESTED.replace('name = "testing"', 'name = "do-testing"'), ())
    assert messages(tree) == []


def test_a_problem_is_where_and_what() -> None:
    assert Problem(where="a/manifest.toml", message="m") == Problem("a/manifest.toml", "m")


def test_a_name_must_be_its_directory(tree: Path) -> None:
    (tree / "apps/api").rename(tree / "apps/front")
    assert messages(tree) == [
        "apps/front/manifest.toml: package.name 'api' is not the directory name 'front'"
    ]


def test_a_nested_testing_package_is_named_after_its_country(tree: Path, write: Writer) -> None:
    write("countries/do/testing", NESTED, ())
    assert messages(tree) == [
        "countries/do/testing/manifest.toml: package.name 'testing' is not 'do-testing'"
    ]


def test_schemas_prefixes_permissions_events_consumers_jobs_and_http_prefixes_are_unique(
    tree: Path, write: Writer
) -> None:
    write(
        "modules/mirror",
        (tree / IDENTITY / "manifest.toml")
        .read_text(encoding="utf-8")
        .replace('name = "identity"', 'name = "mirror"'),
        (),
    )
    assert messages(tree) == [
        "modules/mirror/manifest.toml: event type 'identity.company.registered' is published by identity too",
        "modules/mirror/manifest.toml: http prefix '/v1/companies' is owned by identity too",
        "modules/mirror/manifest.toml: schema 'identity' is owned by identity too",
    ]


def test_what_a_channel_declares_is_unique_too(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ())
    copy = (
        COUNTRY.replace('name = "do"', 'name = "dx"')
        .replace('key = "do-ecf-v1"', 'key = "dx-ecf-v1"')
        .replace('iso = "DO"', 'iso = "DX"')
        .replace('key = "do"', 'key = "dx"')
    )
    write("countries/dx", copy, ())
    where = "countries/dx/manifest.toml"
    assert messages(tree) == [
        f"{where}: consumer 'do_ecf.companies' is declared by do too",
        f"{where}: event type 'do_ecf.document.accepted' is published by do too",
        f"{where}: http prefix '/v1/do' is owned by do too",
        f"{where}: id prefix 'ecf' is minted by do too",
        f"{where}: job kind 'do_ecf.advance' is declared by do too",
        f"{where}: permission 'do_ecf.documents.read' is declared by do too",
        f"{where}: schedule 'do_ecf.poll' is declared by do too",
        f"{where}: schema 'do_ecf' is owned by do too",
    ]


def test_a_dependency_on_an_unknown_package_or_module_is_reported(tree: Path, edit: Editor) -> None:
    edit(
        IDENTITY,
        'packages = ["kernel", "platform"]',
        'packages = ["kernel", "ghost", "api"]\nmodules = ["ghost"]',
    )
    where = "modules/identity/manifest.toml"
    assert messages(tree) == [
        f"{where}: dependencies.modules names 'ghost', which is not a module",
        f"{where}: dependencies.packages names 'api', which is not a package",
        f"{where}: dependencies.packages names 'ghost', which is not a package",
    ]


def test_consuming_is_a_dependency_edge_and_cycles_are_refused(
    tree: Path, write: Writer, edit: Editor
) -> None:
    write("modules/billing", BILLING, ())
    edit(
        IDENTITY,
        "[http]",
        '[[events.consumes]]\nconsumer = "identity.bills"\ntypes = ["billing.thing.done"]\n\n[http]',
    )
    assert messages(tree) == [
        "modules/billing/manifest.toml: the module graph has a cycle: billing -> identity -> billing"
    ]


def test_a_consumer_of_a_type_nobody_publishes_is_reported(tree: Path, edit: Editor) -> None:
    edit(
        IDENTITY,
        "[http]",
        '[[events.consumes]]\nconsumer = "identity.ghosts"\ntypes = ["ghost.thing.done"]\n\n[http]',
    )
    assert messages(tree) == [
        (
            "modules/identity/manifest.toml: consumer 'identity.ghosts' handles 'ghost.thing.done', "
            "which no package publishes"
        )
    ]


def test_a_schedule_enqueues_a_job_kind_its_package_declares(tree: Path, edit: Editor) -> None:
    edit(PLATFORM, 'enqueues = "platform.purge_done_jobs"', 'enqueues = "platform.nothing"')
    assert messages(tree) == [
        (
            "packages/platform/manifest.toml: schedule 'platform.purge_done_jobs' "
            "enqueues 'platform.nothing', "
            "which platform does not declare"
        )
    ]


def test_cron_schedules_are_not_built_yet(tree: Path, edit: Editor) -> None:
    edit(PLATFORM, 'every = "24h"', 'cron = "0 3 * * *"')
    assert messages(tree) == [
        (
            "packages/platform/manifest.toml: schedule 'platform.purge_done_jobs' uses cron, "
            "which is not built until a schedule needs it (§16.4)"
        )
    ]


def test_a_job_kind_and_a_consumer_carry_their_owners_name(tree: Path, edit: Editor) -> None:
    edit(
        IDENTITY,
        "[http]",
        (
            '[[jobs]]\nkind = "invoicing.export"\n\n[[schedules]]\nname = "invoicing.daily"\nevery = "1h"\n'
            'enqueues = "invoicing.export"\n\n[[events.consumes]]\nconsumer = "invoicing.companies"\n'
            'types = ["identity.company.registered"]\n\n[http]'
        ),
    )
    where = "modules/identity/manifest.toml"
    assert messages(tree) == [
        f"{where}: consumer 'invoicing.companies' does not start with its package's name or channel key",
        f"{where}: job kind 'invoicing.export' does not start with its package's name or channel key",
        f"{where}: schedule 'invoicing.daily' does not start with its package's name or channel key",
    ]


def test_a_channels_names_start_with_its_schema(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY.replace('kind = "do_ecf.advance"', 'kind = "do.advance"'), ())
    where = "countries/do/manifest.toml"
    assert messages(tree) == [
        f"{where}: job kind 'do.advance' does not start with its package's name or channel key",
        f"{where}: schedule 'do_ecf.poll' enqueues 'do_ecf.advance', which do does not declare",
    ]


def test_a_module_submodule_outside_the_layers_is_reported(tree: Path) -> None:
    source = tree / IDENTITY / "src/ledger_identity"
    (source / "utils").mkdir()
    (source / "helpers.py").write_text('"""Helpers."""\n', encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "py.typed").write_text("", encoding="utf-8")
    (source / "assembly.py").write_text('"""Assembly."""\n', encoding="utf-8")
    where = "modules/identity/manifest.toml"
    layers = "(assembly, adapters, application, api, domain, events, _manifest)"
    assert messages(tree) == [
        f"{where}: ledger_identity.helpers is not a layer {layers}",
        f"{where}: ledger_identity.utils is not a layer {layers}",
    ]


def test_only_apps_and_its_own_testing_package_depend_on_a_country(
    tree: Path, write: Writer, edit: Editor
) -> None:
    write("countries/do", COUNTRY, ())
    write(
        "countries/do/testing", NESTED.replace('name = "testing"', 'name = "do-testing"') + DEPENDS_ON_DO, ()
    )
    write(
        "countries/mx/testing",
        NESTED.replace('name = "testing"', 'name = "mx-testing"') + DEPENDS_ON_DO,
        (),
    )
    edit("apps/api", 'packages = ["platform"]', 'packages = ["platform", "do"]')
    edit(IDENTITY, 'packages = ["kernel", "platform"]', 'packages = ["kernel", "platform", "do"]')
    assert messages(tree) == [
        f"countries/mx/testing/manifest.toml: {COUNTRY_DEPENDENCY}",
        f"modules/identity/manifest.toml: {COUNTRY_DEPENDENCY}",
    ]


def test_a_package_never_depends_on_itself(tree: Path, edit: Editor) -> None:
    edit(PLATFORM, 'packages = ["kernel"]', 'packages = ["kernel", "platform"]')
    edit(
        IDENTITY,
        'packages = ["kernel", "platform"]',
        'packages = ["kernel", "platform"]\nmodules = ["identity"]',
    )
    assert messages(tree) == [
        "modules/identity/manifest.toml: dependencies.modules names 'identity', which is the package itself",
        (
            "packages/platform/manifest.toml: dependencies.packages names 'platform', "
            "which is the package itself"
        ),
    ]


def test_packages_never_depend_on_each_other_in_a_cycle(tree: Path) -> None:
    kernel = tree / "packages/kernel/manifest.toml"
    kernel.write_text(
        kernel.read_text(encoding="utf-8") + '\n[dependencies]\npackages = ["platform"]\n', "utf-8"
    )
    assert messages(tree) == [
        "packages/platform/manifest.toml: the package graph has a cycle: platform -> kernel -> platform"
    ]


def root_messages(root: Path) -> list[str]:
    return [f"{problem.where}: {problem.message}" for problem in repository_problems(Workspace.load(root))]


def test_a_tree_without_a_root_pyproject_or_deploy_files_has_no_repository_problem(tree: Path) -> None:
    assert root_messages(tree) == []


def test_an_unpinned_image_is_a_repository_problem(tree: Path) -> None:
    (tree / "deploy/local").mkdir(parents=True)
    (tree / "deploy/local/compose.yaml").write_text(
        "services:\n  db:\n    image: postgres:18.7\n", encoding="utf-8"
    )
    assert root_messages(tree) == [
        "deploy/local/compose.yaml: image 'postgres:18.7' is not pinned in deploy/local/images.toml"
    ]


MEMBERS: Final = """
[tool.uv.workspace]
members = ["packages/*", "modules/*", "apps/*", "tools/*"]
"""


def test_a_workspace_member_without_a_manifest_is_named(tree: Path) -> None:
    (tree / "pyproject.toml").write_text(MEMBERS, encoding="utf-8")
    (tree / "packages/zzprobe/src/ledger_zzprobe").mkdir(parents=True)
    (tree / "packages/zzprobe/pyproject.toml").write_text(
        "[project]\nname = 'ledger-zzprobe'\n", encoding="utf-8"
    )
    (tree / "packages/notes.md").write_text("A file, not a member.\n", encoding="utf-8")
    assert [line for line in root_messages(tree) if "zzprobe" in line or "notes" in line] == [
        "packages/zzprobe: a workspace member without manifest.toml: the gate would leave it out"
    ]


def module_with_settings(name: str) -> str:
    return (
        f'[package]\nname = "{name}"\nkind = "module"\nsummary = "A module."\nowners = ["@o"]\n'
        'settings = true\n\n[dependencies]\npackages = ["kernel", "platform"]\n'
    )


def test_settings_sections_never_shadow_the_platform_s_or_each_other_s(tree: Path, write: Writer) -> None:
    write("modules/http", module_with_settings("http"), ())
    write("modules/stock", module_with_settings("stock"), ())
    write("modules/stock_lots", module_with_settings("stock_lots"), ())
    assert messages(tree) == [
        (
            "modules/http/manifest.toml: settings section 'http' (LEDGER_HTTP_*) would read the platform's "
            "LEDGER_HTTP_HOST"
        ),
        (
            "modules/stock_lots/manifest.toml: settings section 'stock_lots' (LEDGER_STOCK_LOTS_*) "
            "lies inside section 'stock' (LEDGER_STOCK_*)"
        ),
    ]


def test_a_channel_s_section_is_checked_like_a_module_s(tree: Path, write: Writer) -> None:
    write("modules/do_ecf", module_with_settings("do_ecf"), ())
    country = COUNTRY.replace('tier = "bronze"', 'tier = "bronze"\nsettings = true')
    write("countries/do", country, ("regime",))
    assert [line for line in messages(tree) if "settings section" in line] == [
        "modules/do_ecf/manifest.toml: settings section 'do_ecf' (LEDGER_DO_ECF_*) is declared twice"
    ]


def test_a_country_s_code_is_its_directory_and_its_regimes_are_its_own(tree: Path, write: Writer) -> None:
    write(
        "countries/do",
        COUNTRY.replace('iso = "DO"', 'iso = "DM"').replace('key = "do"\n', 'key = "dm"\n'),
        ("regime",),
    )
    assert [line for line in messages(tree) if "[country]" in line or "regime" in line] == [
        "countries/do/manifest.toml: [country] iso 'DM' is not the directory's code, 'DO'",
        (
            "countries/do/manifest.toml: regime 'dm' is not of country 'do': "
            "its key starts with the country's code"
        ),
    ]
