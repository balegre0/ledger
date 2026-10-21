"""signer's schema as ``ledger-migrate`` makes it: at head, equal to the models, under row-level security, its
login granted no more than the repositories need.

How migrations run (the lock, the owner role, offline mode, readiness) is the platform's, tested there; here
the migrate app runs signer's revisions on fresh databases and the worker's template database.
"""

import re
from typing import Final

from pydantic import TypeAdapter
from sqlalchemy import CheckConstraint, String, column, create_engine, select, table
from sqlalchemy.pool import NullPool
lazy import psycopg

from ledger_platform.data.tables import chassis_tables
from ledger_platform.runtime.app import App
from ledger_signer.adapters.postgres.schema import MIGRATIONS
from ledger_signer.adapters.postgres.tables import SignerTable
from ledger_signer.domain.certificates import BackendKind, CertificateStatus, RetiredReason
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

S: Final = "signer"
SIGNER_TABLES: Final = frozenset({"certificates", "custody"})
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events"}
)
MODELS: Final = combined(chassis_tables(S).outbox.metadata, SignerTable.metadata)
"""The chassis's tables of schema ``signer`` and signer's own, as its revision must make them."""
QUOTED: Final = re.compile(r"'([^']*)'")
COMMANDS: Final = ("delete", "insert", "select", "update")
PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "DELETE")
GRANTED: Final = TypeAdapter(bool)
COLUMN: Final = TypeAdapter(str)
UPDATABLE_COLUMNS: Final = (
    "SELECT column_name FROM information_schema.column_privileges WHERE grantee = 'signer_rw'"
    " AND table_schema = 'signer' AND table_name = 'certificates' AND privilege_type = 'UPDATE'"
)


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


def test_the_migrate_app_brings_signer_s_schema_to_head_from_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    assert tables_of(fresh_admin_url, schema=S) == set()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert tables_of(fresh_admin_url, schema=S) == {*SIGNER_TABLES, *CHASSIS_TABLES, "alembic_version"}
    assert version_of(fresh_admin_url, S) == MIGRATIONS.head
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_migrating_again_changes_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert version_of(fresh_admin_url, S) == MIGRATIONS.head
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    assert MIGRATIONS.head == "0001"  # the chassis's two versions and signer's tables, in one revision
    assert pending_changes(superuser(migrated), MODELS, schema=S) is None


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domains_words(
    migrated: WorkerDatabase,
) -> None:
    """Alembic does not compare check constraints: their names and definitions are compared here."""
    checks = check_constraints(superuser(migrated), schema=S)
    declared = {
        str(constraint.name)
        for model in SignerTable.metadata.tables.values()
        for constraint in model.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert declared <= set(checks)  # beside the chassis's own, which its tests pin
    assert set(QUOTED.findall(checks["ck_certificates_backend"])) == {str(kind) for kind in BackendKind}
    assert set(QUOTED.findall(checks["ck_certificates_status"])) == {
        str(status) for status in CertificateStatus
    }
    assert set(QUOTED.findall(checks["ck_certificates_retired_reason"])) == {
        str(reason) for reason in RetiredReason
    }
    assert {
        name: checks[name]
        for name in (
            "ck_certificates_modes",
            "ck_certificates_fingerprint",
            "ck_certificates_validity",
            "ck_certificates_retirement",
        )
    } == {
        "ck_certificates_retirement": (
            "CHECK ((((status = 'active'::text) = ((retired_at IS NULL) AND (retired_reason IS NULL)))"
            " AND ((retired_at IS NULL) = (retired_reason IS NULL))))"
        ),
        "ck_certificates_modes": "CHECK ((serves_test OR serves_live OR (status = 'retired'::text)))",
        "ck_certificates_fingerprint": "CHECK ((octet_length(fingerprint_sha256) = 32))",
        "ck_certificates_validity": "CHECK ((not_after > not_before))",
    }


def test_both_tables_are_under_their_company_s_policies_forced_on_their_owner(
    migrated: WorkerDatabase,
) -> None:
    url = superuser(migrated)
    found = policies(url, schema=S)
    for owner in SIGNER_TABLES:
        assert [policy for name, policy in found if name == owner] == [
            f"{owner}_{command}" for command in COMMANDS
        ]
    assert row_security(url, schema=S) == {
        **dict.fromkeys(SIGNER_TABLES, (True, True)),
        "idempotency_keys": (True, True),
        "audit_events": (True, True),
        "outbox": (False, False),
        "inbox": (False, False),
        "jobs": (False, False),
        "subscriptions": (False, False),
        "alembic_version": (False, False),
    }


def test_every_primary_key_is_the_models_column_for_column(migrated: WorkerDatabase) -> None:
    assert primary_keys(superuser(migrated), schema=S) == {
        "alembic_version": ["version_num"],
        **{model.name: [key.name for key in model.primary_key.columns] for model in MODELS.tables.values()},
    }


def test_one_active_certificate_serves_a_company_in_each_mode(migrated: WorkerDatabase) -> None:
    assert indexes_of(superuser(migrated), "certificates", schema=S) == {
        "certificates_pkey": (
            "CREATE UNIQUE INDEX certificates_pkey ON signer.certificates "
            "USING btree (organization_id, company_id, id)"
        ),
        "certificates_one_active_test": (
            "CREATE UNIQUE INDEX certificates_one_active_test ON signer.certificates "
            "USING btree (company_id) WHERE ((status = 'active'::text) AND serves_test)"
        ),
        "certificates_one_active_live": (
            "CREATE UNIQUE INDEX certificates_one_active_live ON signer.certificates "
            "USING btree (company_id) WHERE ((status = 'active'::text) AND serves_live)"
        ),
    }


async def test_the_signer_s_role_reads_and_writes_only_what_its_repositories_need(
    admin: psycopg.AsyncConnection,
) -> None:
    """A certificate is retired, never deleted; sealed material is written once and deleted on retirement."""
    granted: dict[str, set[str]] = {}
    for name in sorted(SIGNER_TABLES):
        for privilege in PRIVILEGES:
            answer = await admin.execute(
                "SELECT has_table_privilege('signer_rw', %s, %s)", (f"signer.{name}", privilege)
            )
            row = await answer.fetchone()
            if row is not None and GRANTED.validate_python(row[0]):
                granted.setdefault(name, set()).add(privilege)
    assert granted == {
        "certificates": {"SELECT", "INSERT"},  # UPDATE by column only: see below
        "custody": {"SELECT", "INSERT", "DELETE"},
    }


async def test_the_signer_s_role_updates_only_what_a_certificate_s_life_changes(
    admin: psycopg.AsyncConnection,
) -> None:
    """Its modes, status, retirement and version: never its certificate, subject, validity or owner."""
    answer = await admin.execute(UPDATABLE_COLUMNS)
    columns = {COLUMN.validate_python(row[0]) for row in await answer.fetchall()}
    assert columns == {"serves_test", "serves_live", "status", "retired_at", "retired_reason", "version"}
