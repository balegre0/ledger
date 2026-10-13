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
from ledger_platform.runtime.manifest import PackageManifest

type Writer = Callable[[str, str, tuple[str, ...]], Path]

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
assembly = "ledger_country_do.channels.ecf_v1.assembly"
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
    assert '"prometheus-client" = "prometheus_client"' in platform
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
    assert "identity-declared-edges" not in text
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
    assert "[importlinter:contract:billing-declared-edges]" not in text
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
        Path("countries/mx/src/ledger_country_mx/_manifest.py"),
        Path("modules/billing/src/ledger_billing/_manifest.py"),  # every module: its assembly reads it
        Path("apps/api/pyproject.toml"),
    } <= set(generated)
    assert (
        Path("packages/kernel/src/ledger_kernel/_manifest.py") not in generated
    )  # the runtime never reads it
    assert all(text.endswith("\n") for text in generated.values())
