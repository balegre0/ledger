"""The module fixtures (``ledger_testing.modules``), exercised with the platform as the module under test."""

from datetime import date
from decimal import Decimal
from functools import partial
from http import HTTPStatus

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
lazy import httpx

from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency, RoundingMode
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey, RoundingPoint, RoundingRule, Source
from ledger_platform._manifest import MANIFEST
from ledger_platform.config.manifest import ChannelEntry, load_manifest
from ledger_platform.data.migrations import SchemaMigrations, platform_migrations
from ledger_platform.data.notifier import OUTBOX
from ledger_platform.runtime.contexts import ChannelPack, ChannelParts, Module, ModuleParts
from ledger_platform.runtime.registries import CHANNELS, MODULES, REGIMES, Discovered
from ledger_testing.modules import (
    ModuleUnderTest,
    NeedUnmetError,
    QuietSignals,
    SchemaLessModuleError,
    installed_schemas,
)
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_platform.runtime.app import App
lazy from ledger_platform.runtime.contexts import ChannelContext, ModuleContext
lazy from ledger_platform.runtime.registries import Discover
lazy from ledger_testing.postgres import WorkerDatabase

_COUNT = TypeAdapter(int)
_NAMES = TypeAdapter(list[tuple[str]])
KERNEL = '[package]\nname = "kernel"\nkind = "package"\nsummary = "Types."\nowners = ["@o"]\n'


def test_a_module_under_test_owns_a_schema() -> None:
    with pytest.raises(SchemaLessModuleError, match="'kernel' owns no schema"):
        ModuleUnderTest(load_manifest(KERNEL), platform_migrations())


def test_a_module_s_schemas_are_the_platform_s_then_its_own() -> None:
    platform = ModuleUnderTest(MANIFEST, platform_migrations())
    assert platform.schemas == ("platform",)
    books = ModuleUnderTest(MANIFEST, migrations_of("books"))
    assert (books.schema, books.schemas) == ("books", ("platform", "books"))
    assert platform.publishes == frozenset()


def test_the_schemas_of_the_modules_a_module_needs_come_from_their_manifests_before_its_own() -> None:
    """What a module needs is what its manifest depends on, transitively, each after what it depends on."""
    alpha, beta, delta = a_module("alpha", "beta", "delta"), a_module("beta", "delta"), a_module("delta")
    needing = ModuleUnderTest(alpha.manifest, migrations_of("alpha"), discover=found(alpha, beta, delta))
    assert [need.schema for need in needing.needs] == ["delta", "beta"]
    assert needing.schemas == ("platform", "delta", "beta", "alpha")
    assert (needing.schema, needing.owned) == (
        "alpha",
        ("alpha", "delta", "beta"),
    )  # the schemas a test empties
    alone = ModuleUnderTest(delta.manifest, migrations_of("delta"), discover=found(delta))
    assert (alone.needs, alone.owned) == ((), ("delta",))
    assert ModuleUnderTest(MANIFEST, platform_migrations(), discover=found(alpha)).needs == ()


def test_a_module_that_needs_one_not_installed_or_without_revisions_is_a_defect_of_its_suite() -> None:
    alpha, beta = a_module("alpha", "beta"), a_module("beta", migrated=False)
    with pytest.raises(NeedUnmetError, match="'beta' is not installed"):
        _ = ModuleUnderTest(alpha.manifest, migrations_of("alpha"), discover=found(alpha)).needs
    with pytest.raises(NeedUnmetError, match="'beta' names no revisions"):
        _ = ModuleUnderTest(alpha.manifest, migrations_of("alpha"), discover=found(alpha, beta)).needs


def test_the_isolated_schemas_are_those_of_its_own_and_its_needs_manifests_that_isolate_them() -> None:
    vault, alpha = a_module("vault", isolated=True), a_module("alpha", "vault")
    everything = found(vault, alpha)
    needing = ModuleUnderTest(alpha.manifest, migrations_of("alpha"), discover=everything)
    assert (needing.schemas, needing.isolated) == (("platform", "vault", "alpha"), ("vault",))
    assert ModuleUnderTest(vault.manifest, migrations_of("vault"), discover=everything).isolated == ("vault",)
    alone = ModuleUnderTest(a_module("beta").manifest, migrations_of("beta"), discover=everything)
    assert alone.isolated == ()
    assert (needing.is_isolated, alone.is_isolated) == (False, False)
    assert ModuleUnderTest(vault.manifest, migrations_of("vault"), discover=everything).is_isolated
    assert ModuleUnderTest(MANIFEST, platform_migrations(), discover=everything).isolated == ()


def test_the_installed_schemas_are_the_platform_s_then_each_module_s_the_isolated_ones_marked() -> None:
    """What ``ledger-migrate``'s tests make their server's roles from, as ``init.sql`` would."""
    found_here = installed_schemas(found(a_module("vault", isolated=True), a_module("alpha", "vault")))
    assert (found_here.schemas, found_here.isolated) == (("platform", "alpha", "vault"), ("vault",))
    assert installed_schemas(found(a_module("alpha"))).isolated == ()


def test_the_installed_schemas_end_with_each_channel_s() -> None:
    """``ledger-migrate`` migrates a channel's schema after the modules': its roles exist as init.sql's."""
    with_channels = installed_schemas(found(a_module("alpha"), channels=("uy-ecf-v1",)))
    assert (with_channels.schemas, with_channels.isolated) == (("platform", "alpha", "uy_ecf"), ())


def migrations_of(schema: str) -> SchemaMigrations:
    return SchemaMigrations(schema=schema, scripts=f"{schema}_unused:migrations")


def a_module(name: str, *depends: str, migrated: bool = True, isolated: bool = False) -> Module:
    declared = ", ".join(f'"{dependency}"' for dependency in depends)
    isolation = 'isolated_in = "signer"\n' if isolated else ""
    text = (
        f'[package]\nname = "{name}"\nkind = "module"\nsummary = "A module."\nowners = ["@o"]\n'
        f'[dependencies]\npackages = ["platform"]\nmodules = [{declared}]\n[data]\nschema = "{name}"\n'
        f"{isolation}"
    )

    def assemble(context: ModuleContext) -> ModuleParts:
        return ModuleParts(migrations=SchemaMigrations(schema=context.database.schema, scripts="unused:x"))

    return Module(
        manifest=load_manifest(text), assemble=assemble, migrations=migrations_of(name) if migrated else None
    )


def found(*modules: Module, channels: tuple[str, ...] = ()) -> Discover:
    """The entry points of a group: these modules (their dependencies are theirs to name), regime ``uy`` and
    channels of it, by key."""
    groups: dict[str, tuple[Discovered, ...]] = {
        MODULES: tuple(Discovered(m.manifest.package.name, partial(identity, m)) for m in modules),
        REGIMES: (Discovered("uy", partial(identity, uy_regime())),),
        CHANNELS: tuple(Discovered(key, partial(identity, a_pack(key))) for key in channels),
    }
    return lambda group: groups.get(group, ())


def uy_regime() -> RegimeDef:
    """Regime ``uy``, of country UY: what its channels serve."""
    return RegimeDef(
        key=RegimeKey("uy"),
        jurisdiction=Jurisdiction(CountryCode("UY"), None),
        currency=Currency.of("USD"),
        time_zone="UTC",
        labels={Locale("en"): "Uruguay"},
        tax_id_schemes=(),
        tax_categories=(),
        document_types=(),
        rounding=RoundingRule(
            mode=RoundingMode.HALF_UP,
            points=frozenset({RoundingPoint.LINE}),
            places=2,
            line_tolerance=Decimal("0.01"),
        ),
        fields=(),
        parameters={},
        sources=(
            Source(
                title="Probe law", url="https://law.invalid", version="1", retrieved_on=date(2026, 10, 20)
            ),
        ),
    )


def a_pack(key: str) -> ChannelPack:
    """A channel of regime ``uy``, its schema its key without the version (``uy-ecf-v1``: ``uy_ecf``)."""
    entry = ChannelEntry.model_validate(
        {
            "key": key,
            "entry_point": "ledger_country_uy.channels.unused:CHANNEL",
            "schema": key.rsplit("-", 1)[0].replace("-", "_"),
            "valid_from": date(2020, 1, 1),
            "environments": ["test"],
            "document_kinds": ["invoice"],
            "directions": ["sales"],
            "tier": "bronze",
        }
    )

    def assemble(context: ChannelContext) -> ChannelParts:
        return ChannelParts(channel=key, migrations=migrations_of(context.database.schema))

    return ChannelPack(entry=entry, regime="uy", assemble=assemble)


def identity(value: object) -> object:
    return value


def test_the_app_a_module_is_tested_in_assembles_it_and_the_modules_it_depends_on_only() -> None:
    alpha, beta, gamma = a_module("alpha", "beta", "delta"), a_module("beta", "delta"), a_module("gamma")
    delta = a_module("delta")
    everything = found(alpha, beta, gamma, delta)
    migrations = SchemaMigrations(schema="alpha", scripts="unused:x")
    tested = ModuleUnderTest(alpha.manifest, migrations, discover=everything).discovering()
    assert [entry.name for entry in tested(MODULES)] == [
        "alpha",
        "beta",
        "delta",
    ]  # transitively, once each; not gamma
    assert [entry.name for entry in tested(REGIMES)] == ["uy"]  # what is not a module is as installed
    alone = ModuleUnderTest(gamma.manifest, migrations, discover=everything).discovering()
    assert [entry.name for entry in alone(MODULES)] == ["gamma"]


def test_a_package_that_is_not_a_module_is_tested_in_the_app_of_every_installed_module() -> None:
    everything = found(a_module("alpha"), a_module("beta"), channels=("uy-ecf-v1",))
    tested = ModuleUnderTest(MANIFEST, platform_migrations(), discover=everything).discovering()
    assert [entry.name for entry in tested(MODULES)] == ["alpha", "beta"]
    assert tested(CHANNELS) == ()  # a channel is assembled by its own country's suite only


def test_a_module_s_suite_assembles_no_channel() -> None:
    """A channel needs ports and a schema of its own: the suites of modules have neither to give it."""
    alpha = a_module("alpha")
    everything = found(alpha, channels=("uy-ecf-v1",))
    tested = ModuleUnderTest(alpha.manifest, migrations_of("alpha"), discover=everything).discovering()
    assert tested(CHANNELS) == ()


COUNTRY = """[package]
name = "uy"
kind = "country"
summary = "A country."
owners = ["@o"]
[dependencies]
packages = ["platform"]
[country]
iso = "UY"
name = "Uruguay"
currencies = ["UYU"]
time_zone = "UTC"
authority = { name = "Revenue", short = "DGI", url = "https://zra.invalid" }
[[channels]]
key = "uy-ecf-v1"
entry_point = "ledger_country_uy.channels.ecf_v1.assembly:CHANNEL"
schema = "uy_ecf"
valid_from = "2020-01-01"
environments = ["test", "prod"]
live_environments = ["prod"]
document_kinds = ["invoice"]
directions = ["sales"]
tier = "bronze"
[[channels.events.publishes]]
type = "fiscal_channel.requirements.changed"
version = 1
"""


def test_a_country_s_channel_is_tested_with_the_modules_that_serve_its_ports() -> None:
    """A country depends on no module; its channel's suite names those that provide its ports."""
    alpha, beta, gamma = a_module("alpha", "beta"), a_module("beta"), a_module("gamma")
    everything = found(alpha, beta, gamma, channels=("uy-ecf-v1", "uy-other-v1"))
    channel = ModuleUnderTest(
        load_manifest(COUNTRY), migrations_of("uy_ecf"), discover=everything, serving=("alpha",)
    )
    assert (channel.schema, channel.source) == ("uy_ecf", "uy-ecf-v1")
    assert channel.publishes == frozenset({"fiscal_channel.requirements.changed"})
    assert [need.schema for need in channel.needs] == ["beta", "alpha"]
    assert channel.schemas == ("platform", "beta", "alpha", "uy_ecf")
    assert channel.owned == ("uy_ecf", "beta", "alpha")
    tested = channel.discovering()
    assert sorted(entry.name for entry in tested(MODULES)) == ["alpha", "beta"]
    assert [entry.name for entry in tested(CHANNELS)] == ["uy-ecf-v1"]  # its own; never another's
    assert [entry.name for entry in tested(REGIMES)] == ["uy"]
    assert ModuleUnderTest(MANIFEST, platform_migrations()).source == "platform"


def test_a_country_owns_only_the_schemas_of_its_channels() -> None:
    with pytest.raises(SchemaLessModuleError, match="'uy' owns no schema 'books'"):
        ModuleUnderTest(load_manifest(COUNTRY), migrations_of("books"))


def test_quiet_signals_tell_no_one() -> None:
    QuietSignals().signal(OUTBOX)


async def test_the_module_s_units_run_in_its_role_and_its_rows_are_emptied_after(
    module_database: BoundDatabase, migrated: WorkerDatabase, admin: psycopg.AsyncConnection
) -> None:
    async with module_database.unit(None) as unit:
        role = await unit.connection.execute(text("SELECT current_user"))
        assert role.scalar_one() == "platform_rw"
        await unit.connection.execute(
            text("INSERT INTO platform.schedule_leases (name, next_run_at) VALUES ('probe.tick', now())")
        )
    rows = await admin.execute("SELECT count(*) FROM platform.schedule_leases")
    assert _COUNT.validate_python((await rows.fetchone() or (0,))[0]) == 1
    assert migrated.name.startswith("ledger_")


def test_the_settings_point_at_the_worker_s_database_and_the_test_s_provider(
    settings: Settings, migrated: WorkerDatabase
) -> None:
    assert migrated.name in settings.database_url.get_secret_value()
    assert settings.database_direct_url is not None
    assert settings.token_issuer is not None


async def test_the_client_talks_to_the_started_api_app(api: App, client: httpx.AsyncClient) -> None:
    assert api.modules  # every installed module, found by its entry point
    assert (await client.get("/livez")).status_code == HTTPStatus.OK


def test_a_fresh_database_holds_the_module_s_schemas_empty(
    fresh_database_name: str, fresh: Settings, fresh_admin_url: str
) -> None:
    assert fresh_database_name in fresh.database_url.get_secret_value()
    with psycopg.connect(fresh_admin_url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        found = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname = 'platform'").fetchall()
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'platform'"
        ).fetchall()
    assert _NAMES.validate_python(found) == [("platform",)]
    assert _NAMES.validate_python(tables) == []
