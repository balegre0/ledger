import runpy
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Final

from ledger_devtools.generate import (
    GENERATED_HEADER,
    importlinter_text,
    manifest_module_text,
    pyproject_text,
    targets,
)
from ledger_devtools.workspace import Workspace
from ledger_platform.config.manifest import PackageManifest

type Writer = Callable[[str, str, tuple[str, ...]], Path]
type Editor = Callable[[str, str, str], None]

ROOT: Final = Path(__file__).parents[4]
"""The repository: ``ruff format`` reads its configuration from there."""
LINE_LENGTH: Final = 110

QUOTES: Final = r"""
[package]
name = "quotes"
kind = "module"
summary = 'Say "hi".'
owners = ["it's", '''It's "x" and "y"''', '''one "x" it's and it's''', "tab\there, back\\slash, bell\u0007"]
"""
LONG: Final = f"""
[package]
name = "long"
kind = "module"
summary = "{" ".join(["lorem"] * 33)}"
owners = ["@{"x" * 300}", "{"ab " * 60}"]
"""

COUNTRY: Final = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@o"]

[dependencies]
packages = ["kernel", "platform"]
python = [
    "lxml>=6.0",
    "opentelemetry-api>=1.45.1",
    "opentelemetry-sdk>=1.45.1",
    "protobuf>=7.35.0",
    "typing-extensions>=4.15",
]

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
tier = "bronze"
"""
OTHER_COUNTRY: Final = """
[package]
name = "mx"
kind = "country"
summary = "Mexico."
owners = ["@o"]

[country]
iso = "MX"
name = "Mexico"
currencies = ["MXN"]
time_zone = "America/Mexico_City"
authority = { name = "SAT", short = "SAT", url = "https://sat.gob.mx" }
"""
DO_TESTING: Final = """
[package]
name = "do-testing"
kind = "package"
summary = "A fake DGII."
owners = ["@o"]

[dependencies]
packages = ["kernel", "do"]
"""
BILLING: Final = """
[package]
name = "billing"
kind = "module"
summary = "Bills."
owners = ["@o"]

[dependencies]
modules = ["identity"]
"""
FISCAL_KIT: Final = """
[package]
name = "fiscal-kit"
kind = "package"
summary = "The channel contract."
owners = ["@o"]
"""


def test_a_modules_pyproject_comes_from_its_manifest(tree: Path) -> None:
    workspace = Workspace.load(tree)
    text = pyproject_text(workspace.by_name("identity"), workspace)
    assert text.startswith(GENERATED_HEADER)
    assert 'name = "ledger-identity"' in text
    assert '    "ledger-kernel",\n    "ledger-platform",\n    "fastapi>=0.143",\n' in text
    assert '[project.entry-points."ledger.modules"]\nidentity = "ledger_identity.assembly:MODULE"' in text
    assert "ledger-platform = { workspace = true }" in text
    assert 'source_pkgs = ["ledger_identity"]' in text
    assert 'source = ["modules/identity/src/ledger_identity/adapters/postgres/migrations"]' in text
    assert "fail_under = 100" in text
    assert "[tool.deptry" not in text
    assert tomllib.loads(text)["project"]["requires-python"] == ">=3.15"


def test_scripts_and_third_party_module_names_are_written(tree: Path) -> None:
    workspace = Workspace.load(tree)
    platform = pyproject_text(workspace.by_name("platform"), workspace)
    assert '[project.scripts]\nledger-probe = "ledger_platform.observability.probe:main"' in platform
    assert "[tool.deptry.package_module_name_map]" not in platform  # deptry reads - as _ itself
    assert 'source = ["packages/platform/src/ledger_platform/data/revisions"]' in platform
    app = pyproject_text(workspace.by_name("api"), workspace)
    assert '[project.scripts]\nledger-api = "ledger_app_api.main:main"' in app
    kernel = pyproject_text(workspace.by_name("kernel"), workspace)
    assert "dependencies = []\n" in kernel
    assert "[tool.uv.sources]" not in kernel
    assert "[project.scripts]" not in kernel
    assert "source = [" not in kernel
    for text in (platform, app, kernel):
        tomllib.loads(text)


def test_a_country_declares_its_regimes_and_channels_as_entry_points(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ("regime", "channels"))
    workspace = Workspace.load(tree)
    text = pyproject_text(workspace.by_name("do"), workspace)
    assert '[project.entry-points."ledger.countries.regimes"]\ndo = "ledger_country_do.regime:REGIME"' in text
    assert (
        '[project.entry-points."ledger.countries.channels"]\n'
        '"do-ecf-v1" = "ledger_country_do.channels.ecf_v1:CHANNEL"'
    ) in text
    assert '"opentelemetry-api" = "opentelemetry"' in text
    assert '"protobuf" = "google"' in text
    module_map = text.split("[tool.deptry.package_module_name_map]", 1)[1]
    assert "lxml" not in module_map
    assert "typing-extensions" not in module_map
    assert tomllib.loads(text)["project"]["name"] == "ledger-country-do"


def test_a_nested_member_never_declares_workspace_sources(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ())
    write("countries/do/testing", DO_TESTING, ())
    workspace = Workspace.load(tree)
    text = pyproject_text(workspace.by_name("do-testing"), workspace)
    assert 'name = "ledger-country-do-testing"' in text
    assert '    "ledger-kernel",\n    "ledger-country-do",\n' in text
    assert "[tool.uv.sources]" not in text
    assert 'source_pkgs = ["ledger_country_do_testing"]' in text


def test_the_manifest_module_validates_the_manifest_data_at_import(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ())
    workspace = Workspace.load(tree)
    for name in ("identity", "platform", "do"):
        package = workspace.by_name(name)
        text = manifest_module_text(package)
        assert text.startswith('"""GENERATED by ledger-devtools sync')
        module = tree / f"{name}_manifest.py"
        module.write_text(text, encoding="utf-8")
        namespace = runpy.run_path(str(module))
        assert namespace["MANIFEST"] == package.manifest
        assert isinstance(namespace["MANIFEST"], PackageManifest)


def test_manifest_values_of_every_toml_type_are_written_as_ruff_formats_them(
    tree: Path, write: Writer
) -> None:
    write("countries/do", COUNTRY.replace('tier = "bronze"', 'tier = "bronze"\nhttp_prefixes = []'), ())
    workspace = Workspace.load(tree)
    country = manifest_module_text(workspace.by_name("do"))
    assert '                "valid_from": "2019-01-01",\n' in country
    assert '                "http_prefixes": [],\n' in country
    platform = manifest_module_text(workspace.by_name("platform"))
    scripts = (
        '            "scripts": {\n'
        '                "ledger-probe": "ledger_platform.observability.probe:main",\n'
    )
    assert scripts in platform
    identity = manifest_module_text(workspace.by_name("identity"))
    assert '                "version": 1,\n' in identity
    for text in (country, platform, identity):
        assert_ruff_accepts(text)


def assert_ruff_accepts(text: str) -> None:
    """``ruff format`` leaves ``text`` as it is and ``ruff check`` (every rule) finds nothing in it."""
    stdin = ("--stdin-filename", "packages/platform/src/ledger_platform/_manifest.py", "-")
    ruff = (sys.executable, "-m", "ruff")
    formatted = subprocess.run(
        [*ruff, "format", *stdin], input=text, capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert formatted.stdout == text
    checked = subprocess.run(
        [*ruff, "check", "--no-cache", *stdin],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert (checked.returncode, checked.stdout) == (0, "All checks passed!\n")
    assert all(len(line) <= LINE_LENGTH for line in text.splitlines())


def assert_round_trips(tree: Path, name: str) -> None:
    """The generated module, imported, holds the manifest the workspace read."""
    package = Workspace.load(tree).by_name(name)
    module = tree / f"{name}_manifest.py"
    module.write_text(manifest_module_text(package), encoding="utf-8")
    assert runpy.run_path(str(module))["MANIFEST"] == package.manifest


def test_strings_are_quoted_the_way_ruff_quotes_them(tree: Path, write: Writer) -> None:
    write("modules/quotes", QUOTES, ())
    text = manifest_module_text(Workspace.load(tree).by_name("quotes"))
    assert "'Say \"hi\".'" in text
    assert '"it\'s"' in text
    assert '\'It\\\'s "x" and "y"\'' in text
    assert '"one \\"x\\" it\'s and it\'s"' in text
    assert '"tab\\there, back\\\\slash, bell\\x07"' in text
    assert_ruff_accepts(text)
    assert_round_trips(tree, "quotes")


def test_long_strings_are_wrapped_to_the_line_length(tree: Path, write: Writer) -> None:
    write("modules/long", LONG, ())
    text = manifest_module_text(Workspace.load(tree).by_name("long"))
    assert '            "summary": (\n' in text
    assert_ruff_accepts(text)
    assert_round_trips(tree, "long")


def test_booleans_and_empty_tables_are_written_too(tree: Path, write: Writer) -> None:
    write(
        "modules/billing",
        BILLING
        + '\n[data]\nschema = "billing"\n\n[[events.consumes]]\nconsumer = "billing.companies"\n'
        + 'types = ["identity.company.registered"]\nordered = true\n\n[extension_points]\n',
        (),
    )
    text = manifest_module_text(Workspace.load(tree).by_name("billing"))
    assert '                "ordered": True,\n' in text
    assert '        "extension_points": {},\n' in text


def test_importlinter_contracts_follow_the_manifests(tree: Path) -> None:
    text = importlinter_text(Workspace.load(tree))
    assert text.startswith("# GENERATED by ledger-devtools sync")
    assert (
        "root_packages =\n    ledger_app_api\n    ledger_identity\n    ledger_kernel\n    ledger_platform\n"
        in text
    )
    assert (
        "layers =\n    ledger_app_api\n    ledger_identity\n    ledger_platform\n    ledger_kernel\n" in text
    )
    assert "[importlinter:contract:kernel-is-stdlib-only]" in text
    assert "    sqlalchemy\n" in text
    assert "    fastapi\n" in text
    assert "[importlinter:contract:identity-layers]" in text
    assert "    adapters\n    application\n    domain\n    _manifest\n" in text
    assert "[importlinter:contract:identity-is-protected]" in text
    assert "[importlinter:contract:apps-only-compose]" in text
    assert "[importlinter:contract:identity-declared-edges]" in text
    assert "modules-never-import-countries" not in text
    assert "countries-are-independent" not in text
    assert "containers =\n    ledger_platform\nexhaustive = True\nlayers =\n    _manifest\n" in text


def test_platform_layers_list_the_subpackages_that_exist(tree: Path) -> None:
    for child in ("runtime", "http", "data"):
        (tree / "packages/platform/src/ledger_platform" / child).mkdir()
    text = importlinter_text(Workspace.load(tree))
    assert "layers =\n    runtime\n    http\n    data\n    _manifest\n" in text


def test_contracts_over_sources_that_do_not_exist_are_left_out(tree: Path) -> None:
    for name in ("kernel", "platform"):
        source = tree / f"packages/{name}/src/ledger_{name}"
        (source / "__init__.py").unlink()
        source.rmdir()
    text = importlinter_text(Workspace.load(tree))
    assert "kernel-is-stdlib-only" not in text
    assert "platform-layers" not in text


def test_importlinter_contracts_cover_countries_edges_and_the_kit(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ("regime", "channels"))
    write("countries/mx", OTHER_COUNTRY, ())
    write("countries/do/testing", DO_TESTING, ())
    write("modules/billing", BILLING, ("domain", "events"))
    write("packages/fiscal-kit", FISCAL_KIT, ())
    text = importlinter_text(Workspace.load(tree))
    assert (
        "layers =\n    ledger_app_api\n    ledger_billing\n    ledger_identity\n"
        "    ledger_country_do | ledger_country_mx\n    ledger_fiscal_kit\n"
        "    ledger_platform\n    ledger_kernel\n"
    ) in text
    assert "[importlinter:contract:do-regime-is-pure]\n" in text
    assert "source_modules =\n    ledger_country_do.regime\n" in text
    assert "mx-regime-is-pure" not in text
    assert "[importlinter:contract:modules-never-import-countries]" in text
    assert "[importlinter:contract:countries-never-import-modules]" in text
    independent = (
        "[importlinter:contract:countries-are-independent]\n"
        "name = Countries are independent\ntype = independence\n"
    )
    assert independent in text
    assert "[importlinter:contract:billing-declared-edges]" in text
    assert "[importlinter:contract:identity-declared-edges]\n" in text
    assert "source_modules =\n    ledger_billing.domain\n    ledger_billing.events\n" in text
    assert "    ledger_country_do_testing\n" in text
    assert "    lxml\n" in text


def test_importlinter_without_apps_has_no_compose_contract(tree: Path) -> None:
    for path in sorted((tree / "apps").rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    text = importlinter_text(Workspace.load(tree))
    assert "apps-only-compose" not in text
    assert "layers =\n    ledger_identity\n" in text


def test_targets_cover_every_generated_file(tree: Path, write: Writer) -> None:
    write("countries/mx", OTHER_COUNTRY, ())
    write("modules/billing", BILLING, ())
    generated = targets(Workspace.load(tree))
    assert {
        Path(".importlinter"),
        Path("modules/identity/pyproject.toml"),
        Path("modules/identity/src/ledger_identity/_manifest.py"),
        Path("packages/platform/src/ledger_platform/_manifest.py"),
        Path("modules/billing/src/ledger_billing/_manifest.py"),  # every module: its assembly reads it
        Path("apps/api/pyproject.toml"),
    } <= set(generated)
    # the runtime never reads these: the kernel's, a country's without a channel
    assert Path("packages/kernel/src/ledger_kernel/_manifest.py") not in generated
    assert Path("countries/mx/src/ledger_country_mx/_manifest.py") not in generated
    assert all(text.endswith("\n") for text in generated.values())


TESTING: Final = """
[package]
name = "testing"
kind = "package"
summary = "Test helpers."
owners = ["@o"]

[dependencies]
packages = ["platform"]
python = ["pytest>=9.1.1"]
"""


def test_the_testing_package_registers_its_pytest_plugin(tree: Path, write: Writer) -> None:
    write("packages/testing", TESTING, ())
    workspace = Workspace.load(tree)
    text = pyproject_text(workspace.by_name("testing"), workspace)
    assert '[project.entry-points.pytest11]\nledger = "ledger_testing.plugin"\n' in text
    assert "pytest11" not in pyproject_text(workspace.by_name("platform"), workspace)
    assert tomllib.loads(text)["project"]["entry-points"]["pytest11"] == {"ledger": "ledger_testing.plugin"}


def test_distributions_no_module_imports_by_name_are_never_reported_unused(tree: Path, edit: Editor) -> None:
    edit(
        "packages/platform", '"prometheus-client>=0.26.0"]', '"prometheus-client>=0.26.0", "tzdata>=2025.2"]'
    )
    edit(
        "modules/identity",
        'python = ["fastapi>=0.143"]',
        'python = ["fastapi>=0.143", "python-multipart>=0.0.32"]',
    )
    workspace = Workspace.load(tree)
    platform = tomllib.loads(pyproject_text(workspace.by_name("platform"), workspace))
    assert platform["tool"]["deptry"]["per_rule_ignores"] == {"DEP002": ["tzdata"]}
    identity = tomllib.loads(pyproject_text(workspace.by_name("identity"), workspace))
    assert identity["tool"]["deptry"] == {
        "package_module_name_map": {"python-multipart": ["multipart", "python_multipart"]},
        "per_rule_ignores": {"DEP002": ["python-multipart"]},
    }
    kernel = pyproject_text(workspace.by_name("kernel"), workspace)
    assert "per_rule_ignores" not in kernel


def test_a_module_a_module_depends_on_is_never_reported_unused(tree: Path, write: Writer) -> None:
    """A module edge is the manifest's (import-linter checks its imports): for events or migrations too."""
    write("modules/billing", BILLING, ())
    workspace = Workspace.load(tree)
    billing = tomllib.loads(pyproject_text(workspace.by_name("billing"), workspace))
    assert billing["tool"]["deptry"]["per_rule_ignores"] == {"DEP002": ["ledger-identity"]}


def test_every_package_imports_only_the_workspace_packages_it_declares(tree: Path, write: Writer) -> None:
    write("packages/testing", TESTING, ())
    text = importlinter_text(Workspace.load(tree))
    assert (
        "[importlinter:contract:api-declared-edges]\n"
        "name = api imports only the workspace packages it declares\n"
        "type = forbidden\n"
        "source_modules =\n    ledger_app_api\n"
        "forbidden_modules =\n    ledger_identity\n    ledger_kernel\n    ledger_testing\n"
    ) in text
    assert "[importlinter:contract:identity-declared-edges]\n" in text
    assert "source_modules =\n    ledger_kernel\nforbidden_modules =\n    ledger_app_api\n" in text


def test_apps_compose_through_the_platform_s_runtime_only(tree: Path) -> None:
    for child in ("runtime", "http", "data"):
        (tree / "packages/platform/src/ledger_platform" / child).mkdir()
    text = importlinter_text(Workspace.load(tree))
    compose = text.split("[importlinter:contract:apps-only-compose]\n", 1)[1]
    assert (
        "forbidden_modules =\n    ledger_identity.adapters\n    ledger_identity.application\n"
        "    ledger_identity.domain\n    ledger_platform._manifest\n    ledger_platform.data\n"
        "    ledger_platform.http\nallow_indirect_imports = True\n"
    ) in compose


def test_production_never_imports_what_only_dev_tooling_declares(tree: Path, write: Writer) -> None:
    write("countries/do", COUNTRY, ())
    write("countries/do/testing", DO_TESTING + 'python = ["signxml>=5.1.0", "lxml>=6.0"]\n', ())
    text = importlinter_text(Workspace.load(tree))
    tooling = text.split("[importlinter:contract:production-never-imports-test-tooling]\n", 1)[1]
    forbidden = tooling.split("forbidden_modules =\n", 1)[1].split("\n\n", 1)[0]
    assert "    signxml" in forbidden.splitlines()
    assert "    lxml" not in forbidden.splitlines()  # the country declares it: production uses it
    assert "    ledger_country_do_testing" in forbidden.splitlines()


def test_a_country_s_channels_on_disk_are_layered_and_pure(tree: Path, write: Writer) -> None:
    source = write("countries/do", COUNTRY, ("regime", "channels"))
    for layer in ("adapters", "application", "domain"):
        (source / "channels/ecf_v1" / layer).mkdir(parents=True)
    (source / "channels/notes.py").write_text('"""Not a channel."""\n', encoding="utf-8")
    text = importlinter_text(Workspace.load(tree))
    assert (
        "[importlinter:contract:do-channel-ecf_v1-layers]\n"
        "name = Channel ecf_v1 of do is layered\n"
        "type = layers\n"
        "containers =\n    ledger_country_do.channels.ecf_v1\n"
        "exhaustive = True\n"
        "layers =\n    adapters\n    application\n    domain\n"
    ) in text
    pure = text.split("[importlinter:contract:do-channel-ecf_v1-is-pure]\n", 1)[1].split("\n\n", 1)[0]
    assert (
        "source_modules =\n    ledger_country_do.channels.ecf_v1.application\n"
        "    ledger_country_do.channels.ecf_v1.domain\n"
    ) in pure
    forbidden = pure.split("forbidden_modules =\n", 1)[1].splitlines()
    assert {"    lxml", "    ledger_platform", "    ledger_identity"} <= set(forbidden)
    assert not {"    ledger_kernel", "    ledger_country_do"} & set(forbidden)
    assert "notes" not in text


def test_targets_cover_init_sql_and_the_openapi_document_of_each_module_the_api_mounts(
    tree: Path, write: Writer
) -> None:
    write("modules/billing", BILLING, ())
    calls: list[str] = []

    def document() -> dict[str, object]:
        calls.append("built")
        return {"openapi": "3.1.0", "paths": {"/v1/companies": {}, "/v1/bills": {}}}

    generated = targets(Workspace.load(tree), document=document)
    assert generated[Path("deploy/local/postgres/init.sql")].startswith(
        "-- GENERATED by ledger-devtools sync"
    )
    assert '"/v1/companies": {}' in generated[Path("modules/identity/openapi.json")]
    assert Path("modules/billing/openapi.json") not in generated
    assert calls == ["built"]


def test_no_document_is_built_when_no_module_mounts_routes_in_the_api(tree: Path, edit: Editor) -> None:
    edit("modules/identity", 'mounted_in = ["api"]', 'mounted_in = ["worker"]')

    def document() -> dict[str, object]:
        raise AssertionError

    generated = targets(Workspace.load(tree), document=document)
    assert Path("modules/identity/openapi.json") not in generated


def test_each_declared_settings_section_is_an_entry_point(tree: Path, write: Writer, edit: Editor) -> None:
    edit("modules/identity", 'owners = ["@o"]', 'owners = ["@o"]\nsettings = true')
    write("countries/do", COUNTRY.replace('tier = "bronze"', 'tier = "bronze"\nsettings = true'), ("regime",))
    workspace = Workspace.load(tree)
    identity = tomllib.loads(pyproject_text(workspace.by_name("identity"), workspace))
    assert identity["project"]["entry-points"]["ledger.settings"] == {
        "identity": "ledger_identity.assembly:MODULE"
    }
    country = tomllib.loads(pyproject_text(workspace.by_name("do"), workspace))
    assert country["project"]["entry-points"]["ledger.settings"] == {
        "do_ecf": "ledger_country_do.channels.ecf_v1:CHANNEL"
    }
    assert "ledger.settings" not in pyproject_text(workspace.by_name("platform"), workspace)


def test_a_country_gets_its_manifest_module_and_the_platform_only_with_a_channel(
    tree: Path, write: Writer
) -> None:
    own = COUNTRY.replace('packages = ["kernel", "platform"]', 'packages = ["kernel"]')
    write("countries/do", own.split("[[channels]]", 1)[0], ("regime",))
    write("countries/zz", own.replace('"do"', '"zz"').replace('"DO"', '"ZZ"'), ("regime",))
    workspace = Workspace.load(tree)
    generated = targets(workspace, document=dict)
    assert Path("countries/do/src/ledger_country_do/_manifest.py") not in generated
    assert Path("countries/zz/src/ledger_country_zz/_manifest.py") in generated
    assert '"ledger-platform"' not in pyproject_text(workspace.by_name("do"), workspace)
    assert '"ledger-platform"' in pyproject_text(workspace.by_name("zz"), workspace)
