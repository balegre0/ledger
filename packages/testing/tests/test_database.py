import secrets
from typing import Final
lazy from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from pydantic import TypeAdapter

from ledger_platform.config.manifest import load_manifest
from ledger_platform.data.migrations import platform_migrations
from ledger_platform.runtime.contexts import Module, ModuleParts
from ledger_platform.runtime.registries import MODULES, Discovered
from ledger_testing.database import (
    TEST_LOGINS,
    app_url,
    bootstrap,
    create_chassis,
    fresh_database,
    libpq_as,
    migrate,
    migrator_url,
    signer_url,
)
from ledger_testing.modules import installed_schemas
from ledger_testing.postgres import PostgresServer, Template, WorkerDatabase
lazy from ledger_platform.runtime.contexts import ModuleContext

NAMES: Final = TypeAdapter(list[tuple[str]])
WORKER = WorkerDatabase(PostgresServer(host="db", port=5433, user="admin", password="secret"), "ledger_gw1")


def test_tests_connect_as_the_logins_the_apps_use() -> None:
    assert app_url(WORKER) == "postgresql+psycopg://ledger_app:app-password@db:5433/ledger_gw1"
    assert migrator_url(WORKER) == "postgresql+psycopg://ledger_migrator:migrator-password@db:5433/ledger_gw1"
    assert signer_url(WORKER) == "postgresql+psycopg://ledger_signer:signer-password@db:5433/ledger_gw1"
    assert (
        libpq_as(WORKER, role="ledger_app", password="pw") == "postgresql://ledger_app:pw@db:5433/ledger_gw1"
    )


def test_every_login_has_a_test_password() -> None:
    assert (TEST_LOGINS.app, TEST_LOGINS.migrator, TEST_LOGINS.signer) == (
        "app-password",
        "migrator-password",
        "signer-password",
    )


@pytest.fixture
def template(postgres: PostgresServer) -> Iterator[Template]:
    """A database of its own, prepared as a template is (dropped after the test)."""
    name = f"template_{secrets.token_hex(6)}"
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield Template(postgres, name)
    with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def schemas_of(url: str) -> set[str]:
    with psycopg.connect(url) as connection:
        rows = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname IN ('platform', 'books')")
        return {name for (name,) in NAMES.validate_python(rows.fetchall())}


def tables_in(url: str, schema: str) -> set[str]:
    with psycopg.connect(url) as connection:
        rows = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,))
        return {name for (name,) in NAMES.validate_python(rows.fetchall())}


def test_a_template_is_bootstrapped_given_the_chassis_and_migrated_as_production_is(
    template: Template, postgres: PostgresServer
) -> None:
    bootstrap(template, ("platform", "books"), isolated=())
    admin = postgres.libpq(template.name)
    assert schemas_of(admin) == {"platform", "books"}
    create_chassis(template, "books")
    assert {"outbox", "inbox", "jobs"} <= tables_in(admin, "books")
    migrate(template, platform_migrations())
    assert {"routing", "schedule_leases"} <= tables_in(admin, "platform")


def test_a_fresh_database_has_the_schemas_and_nothing_in_them(
    template: Template, postgres: PostgresServer
) -> None:
    bootstrap(template, ("platform", "books"), isolated=())
    name = fresh_database(postgres, ("platform", "books"), isolated=())
    try:
        fresh = postgres.libpq(name)
        assert schemas_of(fresh) == {"platform", "books"}
        assert tables_in(fresh, "books") == set()
    finally:
        with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


VAULT_MANIFEST: Final = (
    '[package]\nname = "vault"\nkind = "module"\nsummary = "Keys."\nowners = ["@o"]\n'
    '[data]\nschema = "vault"\nisolated_in = "signer"\n'
)


def assembled_by_no_test(context: ModuleContext) -> ModuleParts:
    """Never called: the schemas come from the manifest alone."""
    del context
    return ModuleParts()


VAULT: Final = Module(manifest=load_manifest(VAULT_MANIFEST), assemble=assembled_by_no_test)


def only_a_vault(group: str) -> tuple[Discovered, ...]:
    """The entry points of a server with one module installed, isolated in the signer's role."""
    return (Discovered("vault", lambda: VAULT),) if group == MODULES else ()


MEMBERSHIPS: Final = """
SELECT granted.rolname, member.rolname
FROM pg_auth_members AS membership
JOIN pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE granted.rolname IN ('platform_rw', 'vault_rw', 'vault_purge')
  AND member.rolname IN ('ledger_app', 'ledger_signer')
"""
PAIRS: Final = TypeAdapter(set[tuple[str, str]])


def test_the_template_grants_an_isolated_schema_to_the_signer_login(
    template: Template, postgres: PostgresServer
) -> None:
    """``ledger-migrate``'s tests with a module isolated in the signer's role installed, as ``init.sql``."""
    installed = installed_schemas(only_a_vault)
    bootstrap(template, installed.schemas, isolated=installed.isolated)
    with psycopg.connect(postgres.libpq(template.name)) as connection:
        pairs = PAIRS.validate_python(connection.execute(MEMBERSHIPS).fetchall())
    assert pairs == {
        ("platform_rw", "ledger_app"),
        ("platform_rw", "ledger_signer"),  # schedule leases
        ("vault_rw", "ledger_signer"),
        ("vault_purge", "ledger_signer"),
    }
    name = fresh_database(postgres, installed.schemas, isolated=installed.isolated)
    try:
        with psycopg.connect(postgres.libpq(name)) as connection:
            row = connection.execute("SELECT has_database_privilege('ledger_signer', %s, 'CONNECT')", (name,))
            assert row.fetchone() == (True,)
    finally:
        with psycopg.connect(postgres.libpq(), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
