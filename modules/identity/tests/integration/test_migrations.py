"""identity's schema as ``ledger-migrate`` makes it: at head, equal to the models, under row-level security.

How migrations run (the lock, the owner role, offline mode, readiness) is the platform's, tested there; here
the migrate app runs identity's revisions on fresh databases and the worker's template database.
"""

import re
from concurrent.futures import ThreadPoolExecutor
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
lazy from ledger_testing.postgres import WorkerDatabase

S: Final = "identity"
IDENTITY_TABLES: Final = frozenset(
    {"organizations", "companies", "memberships", "branches", "emission_points"}
)
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
    fresh: Settings, fresh_admin_url: str
) -> None:
    assert tables_of(fresh_admin_url, schema=S) == set()
    App("migrate", settings=fresh).migrate()
    assert tables_of(fresh_admin_url, schema=S) == {*IDENTITY_TABLES, *CHASSIS_TABLES, "alembic_version"}
    assert (version_of(fresh_admin_url, S), version_of(fresh_admin_url, "platform")) == (
        MIGRATIONS.head,
        "0001",
    )
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_migrating_again_changes_nothing(fresh: Settings, fresh_admin_url: str) -> None:
    App("migrate", settings=fresh).migrate()
    App("migrate", settings=fresh).migrate()
    assert version_of(fresh_admin_url, S) == MIGRATIONS.head
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_two_migrations_started_at_once_both_succeed(fresh: Settings, fresh_admin_url: str) -> None:
    """Two rollouts that migrate at once: one waits for the other's lock, then finds nothing to do."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [pool.submit(App("migrate", settings=fresh).migrate) for _ in range(2)]
        assert [run.result(timeout=120) for run in runs] == [None, None]
    assert tables_of(fresh_admin_url, schema=S) == {*IDENTITY_TABLES, *CHASSIS_TABLES, "alembic_version"}


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    assert MIGRATIONS.head == "0001"  # v1's two revisions, squashed: nothing was in production
    assert pending_changes(superuser(migrated), MODELS, schema=S) is None


def test_a_schema_that_differs_from_the_models_is_noticed(fresh: Settings, fresh_admin_url: str) -> None:
    App("migrate", settings=fresh).migrate()
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
    """Alembic does not compare an index's predicate: the partial unique index is read back here."""
    assert indexes_of(superuser(migrated), "companies", schema=S) == {
        "companies_pkey": "CREATE UNIQUE INDEX companies_pkey ON identity.companies USING btree (id)",
        "uq_companies_organization": (
            "CREATE UNIQUE INDEX uq_companies_organization ON identity.companies "
            "USING btree (organization_id, id)"
        ),
        "ux_companies_active_tax_id": (
            "CREATE UNIQUE INDEX ux_companies_active_tax_id ON identity.companies "
            "USING btree (tax_scheme, tax_value) "
            "WHERE ((status)::text = 'active'::text)"
        ),
    }
