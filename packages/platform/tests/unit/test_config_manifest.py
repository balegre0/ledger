from datetime import date, timedelta
from types import MappingProxyType

import pytest

from ledger_platform.config.manifest import (
    InvalidManifestError,
    PackageKind,
    load_manifest,
    parse_duration,
)

MODULE = """
[package]
name = "invoicing"
kind = "module"
summary = "Commercial documents."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]
modules = ["identity"]
python = ["fastapi>=0.143", "sqlalchemy[asyncio]>=2.1.4"]

[data]
schema = "invoicing"

[[ids]]
prefix = "inv"
resource = "invoice"

[[permissions]]
code = "invoicing.invoices.read"
summary = "Read commercial documents."
roles = ["owner", "admin", "viewer"]

[[events.publishes]]
type = "invoicing.invoice.issued"
version = 1
public = "invoice.issued"

[[events.consumes]]
consumer = "invoicing.companies"
types = ["identity.company.registered"]
ordered = true

[[jobs]]
kind = "invoicing.export"
deadline = "72h"

[[schedules]]
name = "invoicing.purge_drafts"
every = "24h"
enqueues = "invoicing.export"

[http]
mounted_in = ["api"]
prefixes = ["/v1/invoices"]
"""

COUNTRY = """
[package]
name = "do"
kind = "country"
summary = "Dominican Republic."
owners = ["@balegre0"]

[dependencies]
packages = ["kernel", "platform"]

[country]
iso = "DO"
name = "Dominican Republic"
currencies = ["DOP"]
time_zone = "America/Santo_Domingo"
authority = { name = "Dirección General de Impuestos Internos", short = "DGII", url = "https://dgii.gov.do" }

[[regimes]]
key = "do"
entry_point = "ledger_country_do.regime:REGIME"

[[channels]]
key = "do-ecf-v1"
entry_point = "ledger_country_do.channels.ecf_v1:CHANNEL"
schema = "do_ecf"
valid_from = 2019-01-01
environments = ["testecf", "certecf", "ecf"]
live_environments = ["ecf"]
document_kinds = ["invoice", "credit_note", "debit_note"]
directions = ["sales", "purchase"]
http_prefixes = ["/v1/do"]
tier = "bronze"

[[channels.jobs]]
kind = "do_ecf.advance"
max_attempts = 50
deadline = "15d"
concurrency = 16
"""


APP_HEAD = """
[package]
name = "api"
kind = "app"
summary = "The API."
owners = ["@o"]
"""

APP_SECTION = """
[app]
script = "ledger-api"
role = "api"
"""

A_REGIME = """
[[regimes]]
key = "do"
entry_point = "ledger_invoicing.regime:REGIME"
"""


def test_the_spec_example_of_a_module_reads() -> None:
    manifest = load_manifest(MODULE)
    assert manifest.package.kind is PackageKind.MODULE
    assert manifest.schema_name == "invoicing"
    assert manifest.distribution == "ledger-invoicing"
    assert manifest.import_name == "ledger_invoicing"
    assert manifest.events.consumes[0].ordered is True
    assert manifest.events.consumes[0].max_attempts == 10
    assert manifest.jobs[0].lease == timedelta(seconds=60)
    assert manifest.jobs[0].deadline == timedelta(hours=72)
    assert manifest.schedules[0].every == timedelta(hours=24)


def test_the_spec_example_of_a_country_reads() -> None:
    manifest = load_manifest(COUNTRY)
    assert manifest.distribution == "ledger-country-do"
    channel = manifest.channels[0]
    assert (channel.valid_from, channel.valid_until) == (date(2019, 1, 1), None)
    assert (channel.environments, channel.live_environments) == (("testecf", "certecf", "ecf"), ("ecf",))
    assert channel.document_kinds == ("invoice", "credit_note", "debit_note")
    assert (channel.directions, channel.tier) == (("sales", "purchase"), "bronze")
    assert manifest.channels[0].schema_name == "do_ecf"
    assert manifest.channels[0].jobs[0].concurrency == 16


def test_an_app_names_its_script_and_role() -> None:
    manifest = load_manifest(APP_HEAD + APP_SECTION)
    assert (manifest.distribution, manifest.app is not None and manifest.app.role) == (
        "ledger-app-api",
        "api",
    )


@pytest.mark.parametrize(
    ("text", "location"),
    [
        (MODULE + "\n[surprise]\nx = 1\n", "surprise"),
        (MODULE.replace('code = "invoicing.invoices.read"', 'code = "invoicing.read"'), "permissions.0.code"),
        (MODULE.replace('roles = ["owner", "admin", "viewer"]', 'roles = ["root"]'), "permissions.0.roles.0"),
        (MODULE.replace('every = "24h"', 'every = "24h"\ncron = "0 3 * * *"'), "schedules.0"),
        (MODULE.replace('every = "24h"', ""), "schedules.0"),
        (MODULE.replace('deadline = "72h"', 'deadline = "3 days"'), "jobs.0.deadline"),
        (MODULE.replace('prefixes = ["/v1/invoices"]', 'prefixes = ["/invoices"]'), "http.prefixes.0"),
        (MODULE.replace('prefix = "inv"', 'prefix = "invoice"'), "ids.0.prefix"),
        (MODULE.replace('kind = "module"', 'kind = "app"'), "app"),
        (COUNTRY + "\n[dependencies.extra]\n", "dependencies.extra"),
        (
            COUNTRY.replace(
                'packages = ["kernel", "platform"]', 'packages = ["kernel"]\nmodules = ["identity"]'
            ),
            "dependencies.modules",
        ),
        (COUNTRY.replace('kind = "country"', 'kind = "module"'), "country"),
        (APP_HEAD.replace('kind = "app"', 'kind = "country"'), "country"),
        (MODULE + A_REGIME, "regimes"),
        (APP_HEAD + APP_SECTION + '[data]\nschema = "api"\n', "data"),
        ("not = [toml", "line 1"),
    ],
)
def test_a_bad_manifest_lists_where_it_is_wrong_never_the_value(text: str, location: str) -> None:
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(text)
    assert caught.value.problems[0].startswith(f"{location}:")
    assert str(caught.value).startswith(f"manifest: {location}:")
    assert "3 days" not in str(caught.value)
    assert "/invoices" not in str(caught.value).replace("/v1/invoices", "")


def test_a_bad_id_prefix_is_located_never_echoed() -> None:
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(MODULE.replace('prefix = "inv"', 'prefix = "zzzzzzzz"'))
    assert caught.value.problems[0].startswith("ids.0.prefix:")
    assert "zzzzzzzz" not in str(caught.value)


def test_scripts_cannot_be_changed_after_reading() -> None:
    manifest = load_manifest(APP_HEAD + 'scripts = { "ledger-x" = "ledger_app_api.x:main" }\n' + APP_SECTION)
    scripts = manifest.package.scripts
    assert dict(scripts) == {"ledger-x": "ledger_app_api.x:main"}
    assert isinstance(scripts, MappingProxyType)
    assert isinstance(load_manifest(APP_HEAD + APP_SECTION).package.scripts, MappingProxyType)


def test_a_script_name_has_at_most_forty_characters() -> None:
    longest = "ledger-" + "x" * 33
    assert (
        longest
        in load_manifest(APP_HEAD + f'scripts = {{ "{longest}" = "a:b" }}\n' + APP_SECTION).package.scripts
    )
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(APP_HEAD + f'scripts = {{ "{longest}x" = "a:b" }}\n' + APP_SECTION)
    assert caught.value.problems[0].startswith("package.scripts.")


@pytest.mark.parametrize(("text", "seconds"), [("60s", 60), ("10m", 600), ("1h", 3600), ("15d", 1_296_000)])
def test_durations_read_in_four_units(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)


def test_a_duration_already_read_passes_through() -> None:
    assert parse_duration(timedelta(minutes=5)) == timedelta(minutes=5)


@pytest.mark.parametrize("text", ["0s", "1w", "1.5h", "h", "", 60, "60 s"])
def test_anything_else_is_not_a_duration(text: object) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


def test_a_module_may_declare_a_settings_section_named_after_it() -> None:
    assert load_manifest(MODULE).settings_section is None
    declared = load_manifest(
        MODULE.replace('owners = ["@balegre0"]', 'owners = ["@balegre0"]\nsettings = true')
    )
    assert declared.package.settings
    assert declared.settings_section == "invoicing"


def test_a_channel_s_settings_section_is_named_after_its_schema() -> None:
    channel = load_manifest(COUNTRY).channels[0]
    assert channel.settings_section is None
    declared = load_manifest(COUNTRY.replace('tier = "bronze"', 'tier = "bronze"\nsettings = true'))
    assert declared.channels[0].settings_section == "do_ecf"


@pytest.mark.parametrize(
    "text",
    [
        APP_HEAD.replace('owners = ["@o"]', 'owners = ["@o"]\nsettings = true') + APP_SECTION,
        COUNTRY.replace('owners = ["@balegre0"]', 'owners = ["@balegre0"]\nsettings = true'),
    ],
    ids=["app", "country"],
)
def test_only_a_module_declares_a_settings_section_of_its_package(text: str) -> None:
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(text)
    assert caught.value.problems == (
        "package.settings: only a module declares a settings section of its own",
    )


@pytest.mark.parametrize(
    ("old", "new", "location"),
    [
        ('schema = "invoicing"', 'schema = "public"', "data.schema"),
        ('schema = "invoicing"', 'schema = "select"', "data.schema"),
        ('type = "invoicing.invoice.issued"', 'type = "invoicing.issued"', "events.publishes.0.type"),
        ('prefix = "inv"', 'prefix = "i1"', "ids.0.prefix"),
        ('code = "invoicing.invoices.read"', 'code = "invoicing.invoices.peek"', "permissions.0.code"),
    ],
)
def test_names_are_read_with_the_kernel_s_and_the_roles_rules(old: str, new: str, location: str) -> None:
    with pytest.raises(InvalidManifestError) as caught:
        load_manifest(MODULE.replace(old, new))
    assert caught.value.problems[0].startswith(f"{location}: ")
    assert new.split('"')[1] not in str(caught.value)


def test_extension_points_name_protocols_and_qualified_implementations() -> None:
    good = MODULE + '[extension_points]\ndeclares = ["IssuePolicy"]\nimplements = ["fiscal.IssuePolicy"]\n'
    assert load_manifest(good).extension_points.implements == ("fiscal.IssuePolicy",)
    for bad, location in (
        ('declares = ["issue policy"]', "extension_points.declares.0"),
        ('implements = ["IssuePolicy"]', "extension_points.implements.0"),
        ('provides = ["signer-port"]', "extension_points.provides.0"),
    ):
        with pytest.raises(InvalidManifestError) as caught:
            load_manifest(MODULE + f"[extension_points]\n{bad}\n")
        assert caught.value.problems[0].startswith(f"{location}: ")
