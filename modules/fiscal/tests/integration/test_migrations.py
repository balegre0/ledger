"""fiscal's schema as ``ledger-migrate`` makes it: at head, equal to the models, under row-level security, its
role granted no more than the repository needs.

How migrations run (the lock, the owner role, offline mode, readiness) is the platform's, tested there; here
the migrate app runs fiscal's revisions on a fresh database and the worker's template database.
"""

import re
from typing import Final

from pydantic import TypeAdapter
from sqlalchemy import CheckConstraint, String, column, create_engine, select, table
from sqlalchemy.pool import NullPool
lazy import psycopg

from ledger_fiscal.adapters.postgres.schema import MIGRATIONS
from ledger_fiscal.adapters.postgres.tables import FiscalTable
from ledger_fiscal.domain.enrollments import EnrollmentStatus
from ledger_kernel.tenancy import Mode
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

S: Final = "fiscal"
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events"}
)
MODELS: Final = combined(chassis_tables(S).outbox.metadata, FiscalTable.metadata)
"""The chassis's tables of schema ``fiscal`` and fiscal's own, as its revision must make them."""
QUOTED: Final = re.compile(r"'([^']*)'")
PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "DELETE")
GRANTED: Final = TypeAdapter(bool)


def superuser(worker: WorkerDatabase) -> str:
    """The worker's database as the superuser (SQLAlchemy URL): what the catalog readers connect as."""
    return worker.url(role=worker.server.user, password=worker.server.password)


def version_of(url: str) -> str:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            version = column("version_num", String)
            versions = table("alembic_version", version, schema=S)
            return connection.execute(select(version).select_from(versions)).scalar_one()
    finally:
        engine.dispose()


def test_the_migrate_app_brings_fiscal_s_schema_to_head_from_nothing_and_again_changes_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    assert tables_of(fresh_admin_url, schema=S) == set()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert tables_of(fresh_admin_url, schema=S) == {
        "enrollments",
        "routings",
        *CHASSIS_TABLES,
        "alembic_version",
    }
    assert version_of(fresh_admin_url) == MIGRATIONS.head == "0002"
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    url = superuser(migrated)
    assert pending_changes(url, MODELS, schema=S) is None
    assert primary_keys(url, schema=S) == {
        "alembic_version": ["version_num"],
        **{model.name: [key.name for key in model.primary_key.columns] for model in MODELS.tables.values()},
    }


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domain_s_words(
    migrated: WorkerDatabase,
) -> None:
    """Alembic does not compare check constraints: their names and definitions are compared here."""
    checks = check_constraints(superuser(migrated), schema=S)
    declared = {
        str(constraint.name)
        for model in FiscalTable.metadata.tables.values()
        for constraint in model.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert declared <= set(checks)
    assert set(QUOTED.findall(checks["ck_enrollments_mode"])) == {str(mode) for mode in Mode}
    assert set(QUOTED.findall(checks["ck_enrollments_status"])) == {
        str(status) for status in EnrollmentStatus
    }
    assert checks["ck_enrollments_version"] == "CHECK ((version > 0))"
    assert set(QUOTED.findall(checks["ck_routings_mode"])) == {str(mode) for mode in Mode}
    assert checks["ck_enrollments_disabled"] == (
        "CHECK ((((status = 'disabled'::text) = "
        "((disabled_at IS NOT NULL) AND (disabled_reason IS NOT NULL)))"
        " AND ((disabled_at IS NULL) = (disabled_reason IS NULL))))"
    )


def test_one_enrollment_not_disabled_per_company_mode_and_channel(migrated: WorkerDatabase) -> None:
    assert indexes_of(superuser(migrated), "enrollments", schema=S) == {
        "enrollments_pkey": (
            "CREATE UNIQUE INDEX enrollments_pkey ON fiscal.enrollments "
            "USING btree (organization_id, company_id, mode, id)"
        ),
        "enrollments_one_per_channel": (
            "CREATE UNIQUE INDEX enrollments_one_per_channel ON fiscal.enrollments "
            "USING btree (company_id, mode, channel) WHERE (status <> 'disabled'::text)"
        ),
    }


def test_enrollments_are_under_their_tenant_s_policies_forced_on_their_owner(
    migrated: WorkerDatabase,
) -> None:
    url = superuser(migrated)
    assert [name for owner, name in policies(url, schema=S) if owner == "enrollments"] == [
        f"enrollments_{command}" for command in ("delete", "insert", "select", "update")
    ]
    assert [name for owner, name in policies(url, schema=S) if owner == "routings"] == [
        "routings_insert",
        "routings_select",
    ]
    assert row_security(url, schema=S) == {
        "enrollments": (True, True),
        "routings": (True, True),
        "idempotency_keys": (True, True),
        "audit_events": (True, True),
        "outbox": (False, False),
        "inbox": (False, False),
        "jobs": (False, False),
        "subscriptions": (False, False),
        "alembic_version": (False, False),
    }


async def granted_on(admin: psycopg.AsyncConnection, table_name: str) -> set[str]:
    """What ``fiscal_rw`` may do to ``table_name`` of fiscal's schema."""
    granted: set[str] = set()
    for privilege in PRIVILEGES:
        answer = await admin.execute(
            "SELECT has_table_privilege('fiscal_rw', %s, %s)", (f"fiscal.{table_name}", privilege)
        )
        row = await answer.fetchone()
        if row is not None and GRANTED.validate_python(row[0]):
            granted.add(privilege)
    return granted


async def test_fiscal_s_role_never_deletes_an_enrollment(admin: psycopg.AsyncConnection) -> None:
    """An enrollment is disabled, never deleted: its id stays what an event and an audit row named."""
    assert await granted_on(admin, "enrollments") == {"SELECT", "INSERT", "UPDATE"}


async def test_a_routing_is_kept_once_and_never_changed(admin: psycopg.AsyncConnection) -> None:
    """A routing names the enrollment a document was sent for: routing again finds it, as it was kept."""
    assert await granted_on(admin, "routings") == {"SELECT", "INSERT"}
