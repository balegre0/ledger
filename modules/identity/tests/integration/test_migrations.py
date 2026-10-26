"""identity's schema as ``ledger-migrate`` makes it: at head, equal to the models, under row-level security.

How migrations run (the lock, the owner role, offline mode, readiness) is the platform's, tested there; here
the migrate app runs identity's revisions on fresh databases and the worker's template database.
"""

import os
import re
import subprocess
import sys
from typing import Final

from sqlalchemy import CheckConstraint, String, column, create_engine, select, table, text
from sqlalchemy.pool import NullPool

from ledger_identity.adapters.postgres.schema import MIGRATIONS
from ledger_identity.adapters.postgres.tables import IdentityTable
from ledger_identity.domain.company import CompanyStatus
from ledger_identity.domain.membership import MembershipStatus
from ledger_kernel.access import Role
from ledger_platform.data.tables import chassis_tables
from ledger_platform.runtime.app import App
from ledger_testing.migrations import (
    check_constraints,
    combined,
    indexes_of,
    pending_changes,
    policies,
    primary_keys,
    row_security,
    tables_of,
)
lazy from ledger_platform.config.settings import Settings
lazy from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import WorkerDatabase

S: Final = "identity"
IDENTITY_TABLES: Final = frozenset(
    {"organizations", "companies", "company_tax_ids", "memberships", "establishments", "api_clients"}
)
OUTSIDE_RLS: Final = frozenset({"tax_registrations"})
"""Identity's tables no tenant reads: the global registry of the tax ids active companies hold (§6.3)."""
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events"}
)
CHASSIS: Final = chassis_tables(S)
MODELS: Final = combined(CHASSIS.outbox.metadata, IdentityTable.metadata)
"""The chassis's tables of schema ``identity`` and identity's own, as its revision must make them."""
QUOTED: Final = re.compile(r"'([^']*)'")
"""A literal in a constraint's definition as PostgreSQL prints it back: ``'owner'::character varying``."""
COMMANDS: Final = ("delete", "insert", "select", "update")


def superuser(worker: WorkerDatabase) -> str:
    """The worker's database as the superuser (SQLAlchemy URL): what the catalog readers connect as."""
    return worker.url(role=worker.server.user, password=worker.server.password)


def version_of(url: str, schema: str) -> str:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            version = column("version_num", String)
            versions = table("alembic_version", version, schema=schema)
            return connection.execute(select(version).select_from(versions)).scalar_one()
    finally:
        engine.dispose()


def test_the_migrate_app_brings_identity_s_schema_to_head_from_nothing(
    fresh: Settings,
    fresh_admin_url: str,
    module_under_test: ModuleUnderTest,
) -> None:
    assert tables_of(fresh_admin_url, schema=S) == set()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert tables_of(fresh_admin_url, schema=S) == {
        *IDENTITY_TABLES,
        *OUTSIDE_RLS,
        *CHASSIS_TABLES,
        "alembic_version",
    }
    assert (version_of(fresh_admin_url, S), version_of(fresh_admin_url, "platform")) == (
        MIGRATIONS.head,
        "0002",
    )
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_migrating_again_changes_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert version_of(fresh_admin_url, S) == MIGRATIONS.head
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_two_migrations_started_at_once_both_succeed(fresh: Settings, fresh_admin_url: str) -> None:
    """Two rollouts that migrate at once: one waits for the other's lock, then finds nothing to do.

    Each in a process of its own, as two rollouts run: Alembic's ``op`` and ``context`` are one per process,
    so two migrations in two threads of one process would share them.
    """
    assert fresh.database_direct_url is not None
    environment = {
        name: value for name, value in os.environ.items() if not name.upper().startswith("LEDGER_")
    }
    environment |= {
        "LEDGER_ENVIRONMENT": "test",
        "LEDGER_DATABASE_URL": fresh.database_url.get_secret_value(),
        "LEDGER_DATABASE_DIRECT_URL": fresh.database_direct_url.get_secret_value(),
    }
    script = (
        "from ledger_platform.config.settings import Settings\n"
        "from ledger_platform.runtime.app import App\n"
        "from ledger_platform.runtime.registries import CHANNELS, MODULES, installed\n"
        "def only_identity(group):\n"
        "    if group == CHANNELS:\n"  # a channel reads ports no module here provides
        "        return []\n"
        "    return [e for e in installed(group) if group != MODULES or e.name == 'identity']\n"
        "App('migrate', settings=Settings(), discover=only_identity).migrate()\n"
    )
    command = [sys.executable, "-c", script]
    runs = [subprocess.Popen(command, env=environment) for _ in range(2)]
    assert [run.wait(timeout=120) for run in runs] == [0, 0]
    assert tables_of(fresh_admin_url, schema=S) == {
        *IDENTITY_TABLES,
        *OUTSIDE_RLS,
        *CHASSIS_TABLES,
        "alembic_version",
    }


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    assert (
        MIGRATIONS.head == "0004"
    )  # 0001 squashes v1's two; 0002 chassis v2; 0003 API clients' policies; 0004 addresses
    assert pending_changes(superuser(migrated), MODELS, schema=S) is None


def test_a_schema_that_differs_from_the_models_is_noticed(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    engine = create_engine(fresh_admin_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX identity.ix_memberships_company"))
    finally:
        engine.dispose()
    assert "ix_memberships_company" in (pending_changes(fresh_admin_url, MODELS, schema=S) or "")


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domains_words(
    migrated: WorkerDatabase,
) -> None:
    """Alembic does not compare check constraints: their names and the words they allow are compared here."""
    checks = check_constraints(superuser(migrated), schema=S)
    declared = {
        str(constraint.name)
        for table in IdentityTable.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert declared <= set(checks)  # beside the chassis's own, which its tests pin
    for name, words in {
        "ck_companies_status": CompanyStatus,
        "ck_memberships_role": Role,
        "ck_memberships_status": MembershipStatus,
    }.items():
        assert set(QUOTED.findall(checks[name])) == {str(word) for word in words}, name
    assert {name: checks[name] for name in ("ck_companies_version", "ck_memberships_version")} == {
        "ck_companies_version": "CHECK ((version > 0))",
        "ck_memberships_version": "CHECK ((version > 0))",
    }
    assert checks["ck_companies_address"] == (
        "CHECK (((address_line IS NOT NULL)"
        " OR ((address_municipality IS NULL) AND (address_province IS NULL))))"
    )


def test_every_table_of_a_tenant_s_data_is_under_its_policies_forced_on_its_owner(
    migrated: WorkerDatabase,
) -> None:
    url = superuser(migrated)
    found = policies(url, schema=S)
    for owner in IDENTITY_TABLES:
        assert [policy for name, policy in found if name == owner] == [
            f"{owner}_{command}" for command in COMMANDS
        ]
    assert row_security(url, schema=S) == {
        **dict.fromkeys(IDENTITY_TABLES, (True, True)),
        **dict.fromkeys(OUTSIDE_RLS, (False, False)),  # no tenant's rows: every organization's, by design
        "idempotency_keys": (True, True),
        "audit_events": (True, True),
        "outbox": (False, False),  # the queues are the module's own and the relay's, no tenant's to read
        "inbox": (False, False),
        "jobs": (False, False),
        "subscriptions": (False, False),
        "alembic_version": (False, False),
    }


def test_every_primary_key_is_the_models_column_for_column(migrated: WorkerDatabase) -> None:
    """Alembic does not compare primary keys: their columns, in order, are compared here."""
    assert primary_keys(superuser(migrated), schema=S) == {
        "alembic_version": ["version_num"],
        **{
            table.name: [column.name for column in table.primary_key.columns]
            for table in MODELS.tables.values()
        },
    }


def test_one_active_company_holds_a_tax_id_in_every_organization(migrated: WorkerDatabase) -> None:
    """The global registry's primary key is the tax id: the scheme and the value, in every organization."""
    assert indexes_of(superuser(migrated), "tax_registrations", schema=S) == {
        "tax_registrations_pkey": (
            "CREATE UNIQUE INDEX tax_registrations_pkey ON identity.tax_registrations "
            "USING btree (scheme, value)"
        ),
        "ix_tax_registrations_company": (
            "CREATE INDEX ix_tax_registrations_company ON identity.tax_registrations USING btree (company_id)"
        ),
    }


def test_a_company_no_longer_carries_the_partial_index_that_made_its_tax_id_unique(
    migrated: WorkerDatabase,
) -> None:
    assert indexes_of(superuser(migrated), "companies", schema=S) == {
        "companies_pkey": "CREATE UNIQUE INDEX companies_pkey ON identity.companies USING btree (id)",
        "uq_companies_organization": (
            "CREATE UNIQUE INDEX uq_companies_organization ON identity.companies "
            "USING btree (organization_id, id)"
        ),
    }
