"""The module fixtures (``ledger_testing.modules``), exercised with the platform as the module under test."""

from functools import partial
from http import HTTPStatus

import psycopg
import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
lazy import httpx

from ledger_platform._manifest import MANIFEST
from ledger_platform.config.manifest import load_manifest
from ledger_platform.data.migrations import SchemaMigrations, platform_migrations
from ledger_platform.data.notifier import OUTBOX
from ledger_platform.runtime.contexts import Module, ModuleParts
from ledger_platform.runtime.registries import MODULES, REGIMES, Discovered
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
lazy from ledger_platform.runtime.contexts import ModuleContext
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


def found(*modules: Module) -> Discover:
    """The entry points of a group: these modules (their dependencies are theirs to name), one regime key."""
    groups: dict[str, tuple[Discovered, ...]] = {
        MODULES: tuple(Discovered(m.manifest.package.name, partial(identity, m)) for m in modules),
        REGIMES: (Discovered("zz", partial(identity, "a regime")),),
    }
    return lambda group: groups.get(group, ())


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
    assert [entry.name for entry in tested(REGIMES)] == ["zz"]  # what is not a module is as installed
    alone = ModuleUnderTest(gamma.manifest, migrations, discover=everything).discovering()
    assert [entry.name for entry in alone(MODULES)] == ["gamma"]


def test_a_package_that_is_not_a_module_is_tested_in_the_app_of_every_installed_module() -> None:
    everything = found(a_module("alpha"), a_module("beta"))
    tested = ModuleUnderTest(MANIFEST, platform_migrations(), discover=everything).discovering()
    assert [entry.name for entry in tested(MODULES)] == ["alpha", "beta"]


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
