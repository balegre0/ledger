"""Schema ``do_ecf`` as ``ledger-migrate`` makes it: at head, equal to the models, every tenant table under
row-level security, its role granted no more than the repositories need.

How migrations run (the lock, the owner role, offline mode, readiness) is the platform's, tested there; here
the migrate app runs the channel's revisions on a fresh database and the worker's template database.
"""

import re
from datetime import UTC, datetime
from typing import Final

import pytest
from pydantic import TypeAdapter
from sqlalchemy import CheckConstraint, String, column, create_engine, insert, select, table, text
from sqlalchemy.pool import NullPool
lazy import psycopg

from ledger_country_do.channels.ecf_v1.adapters.postgres.migrations.versions import r0003_do_ecf
from ledger_country_do.channels.ecf_v1.adapters.postgres.schema import MIGRATIONS
from ledger_country_do.channels.ecf_v1.adapters.postgres.tables import DoEcfTable
from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.numbering import RangeStatus
from ledger_country_do.channels.ecf_v1.domain.routing import Route
from ledger_country_do.channels.ecf_v1.domain.status_map import NumberFate
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
lazy from ledger_platform.data.bound import BoundDatabase
lazy from ledger_testing.modules import ModuleUnderTest
lazy from ledger_testing.postgres import WorkerDatabase

S: Final = "do_ecf"
TENANT_TABLES: Final = frozenset(
    {"numbering_series", "numbering_ranges", "released_numbers", "documents", "submissions"}
)
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events"}
)
MODELS: Final = combined(chassis_tables(S).outbox.metadata, DoEcfTable.metadata)
"""The chassis's tables of schema ``do_ecf`` and the channel's own, as its revision must make them."""
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


@pytest.mark.usefixtures("channel_environment")
def test_the_migrate_app_brings_the_channel_s_schema_to_head_from_nothing_and_again_changes_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    """As a rollout runs it: no archive is configured, and ``ledger-migrate`` needs none."""
    assert fresh.archive_backend is None
    assert tables_of(fresh_admin_url, schema=S) == set()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert tables_of(fresh_admin_url, schema=S) == {*TENANT_TABLES, *CHASSIS_TABLES, "alembic_version"}
    assert version_of(fresh_admin_url) == MIGRATIONS.head == "0004"
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert pending_changes(fresh_admin_url, MODELS, schema=S) is None


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    url = superuser(migrated)
    assert pending_changes(url, MODELS, schema=S) is None
    assert primary_keys(url, schema=S) == {
        "alembic_version": ["version_num"],
        **{model.name: [key.name for key in model.primary_key.columns] for model in MODELS.tables.values()},
    }


def words(checks: dict[str, str], name: str) -> set[str]:
    return set(QUOTED.findall(checks[name]))


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domain_s_words(
    migrated: WorkerDatabase,
) -> None:
    """Alembic does not compare check constraints: their names and definitions are compared here."""
    checks = check_constraints(superuser(migrated), schema=S)
    declared = {
        str(constraint.name)
        for model in DoEcfTable.metadata.tables.values()
        for constraint in model.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert declared <= set(checks)
    modes, environments, types = {str(mode) for mode in Mode}, set(Environment), set(EcfType)
    for owner in TENANT_TABLES:
        assert words(checks, f"ck_{owner}_mode") == modes
        assert words(checks, f"ck_{owner}_environment") == environments
        assert words(checks, f"ck_{owner}_ecf_type") == types
    assert words(checks, "ck_numbering_ranges_status") == set(RangeStatus)
    assert words(checks, "ck_documents_state") == set(EcfDocumentState)
    assert words(checks, "ck_documents_route") == set(Route)
    assert words(checks, "ck_documents_delivery_state") == set(DeliveryState)
    assert words(checks, "ck_documents_emission_mode") == set(EmissionMode)
    assert words(checks, "ck_documents_number_fate") == set(NumberFate)
    assert checks["ck_numbering_ranges_numbers"] == (
        "CHECK (((1 <= first) AND (first <= next) AND (next <= (last + 1))"
        " AND (last <= '9999999999'::bigint)))"
    )


def test_a_series_ranges_are_found_by_their_series(migrated: WorkerDatabase) -> None:
    assert indexes_of(superuser(migrated), "numbering_ranges", schema=S) == {
        "numbering_ranges_pkey": (
            "CREATE UNIQUE INDEX numbering_ranges_pkey ON do_ecf.numbering_ranges "
            "USING btree (organization_id, company_id, mode, id)"
        ),
        "numbering_ranges_series": (
            "CREATE INDEX numbering_ranges_series ON do_ecf.numbering_ranges "
            "USING btree (organization_id, company_id, mode, environment, ecf_type)"
        ),
        "numbering_ranges_series_key": (
            "CREATE UNIQUE INDEX numbering_ranges_series_key ON do_ecf.numbering_ranges "
            "USING btree (organization_id, company_id, mode, environment, ecf_type, id)"
        ),
    }


def test_a_document_is_found_by_its_invoice_and_its_number_held_once(migrated: WorkerDatabase) -> None:
    """One document per invoice (Decision 35); a number held by one document until DGII releases it."""
    assert indexes_of(superuser(migrated), "documents", schema=S) == {
        "documents_pkey": (
            "CREATE UNIQUE INDEX documents_pkey ON do_ecf.documents "
            "USING btree (organization_id, company_id, mode, id)"
        ),
        "documents_invoice_key": (
            "CREATE UNIQUE INDEX documents_invoice_key ON do_ecf.documents "
            "USING btree (organization_id, company_id, mode, invoice_id)"
        ),
        "documents_number": (
            "CREATE UNIQUE INDEX documents_number ON do_ecf.documents "
            "USING btree (organization_id, company_id, mode, environment, ecf_type, sequence) "
            "WHERE (number_fate IS DISTINCT FROM 'released'::text)"
        ),
    }


def test_a_document_s_track_ids_are_found_by_its_number(migrated: WorkerDatabase) -> None:
    """Decision 9: the track ids of every document that held an e-NCF, asked for by the number."""
    assert indexes_of(superuser(migrated), "submissions", schema=S) == {
        "submissions_pkey": (
            "CREATE UNIQUE INDEX submissions_pkey ON do_ecf.submissions "
            "USING btree (organization_id, company_id, mode, document_id, track_id)"
        ),
        "submissions_number": (
            "CREATE INDEX submissions_number ON do_ecf.submissions "
            "USING btree (organization_id, company_id, mode, environment, ecf_type, sequence)"
        ),
    }


def test_every_tenant_table_is_under_its_tenant_s_policies_forced_on_its_owner(
    migrated: WorkerDatabase,
) -> None:
    url = superuser(migrated)
    for owner in TENANT_TABLES:
        assert [name for table_name, name in policies(url, schema=S) if table_name == owner] == [
            f"{owner}_{command}" for command in ("delete", "insert", "select", "update")
        ]
    assert row_security(url, schema=S) == {
        **dict.fromkeys(TENANT_TABLES, (True, True)),
        "idempotency_keys": (True, True),
        "audit_events": (True, True),
        "outbox": (False, False),
        "inbox": (False, False),
        "jobs": (False, False),
        "subscriptions": (False, False),
        "alembic_version": (False, False),
    }


async def granted(admin: psycopg.AsyncConnection, table_name: str) -> set[str]:
    found: set[str] = set()
    for privilege in PRIVILEGES:
        answer = await admin.execute(
            "SELECT has_table_privilege('do_ecf_rw', %s, %s)", (f"do_ecf.{table_name}", privilege)
        )
        row = await answer.fetchone()
        if row is not None and GRANTED.validate_python(row[0]):
            found.add(privilege)
    return found


async def test_the_channel_s_role_deletes_nothing_but_a_released_number_taken(
    admin: psycopg.AsyncConnection,
) -> None:
    """A range and its series are kept for ever (its numbers are accounted for); audit rows too."""
    assert await granted(admin, "numbering_series") == {"SELECT", "INSERT", "UPDATE"}
    assert await granted(admin, "numbering_ranges") == {"SELECT", "INSERT", "UPDATE"}
    assert await granted(admin, "released_numbers") == {"SELECT", "INSERT", "DELETE"}
    assert await granted(admin, "documents") == {"SELECT", "INSERT", "UPDATE"}
    assert await granted(admin, "submissions") == {"SELECT", "INSERT"}
    assert await granted(admin, "audit_events") == {"SELECT", "INSERT"}


async def test_revision_0003_takes_on_the_signed_documents_task_18_s_job_left_waiting(
    module_database: BoundDatabase,
) -> None:
    """Task 18's handler parked a signed document for submission (``submission``); 0003 makes it ready."""
    jobs = chassis_tables(S).jobs
    now = datetime.now(UTC)
    waiting = {"kind": "do_ecf.advance", "status": "parked", "parked_reason": "submission"}
    rows = [
        waiting | {"key": "waiting"},
        waiting | {"key": "blocked", "parked_reason": "blocked"},
        waiting | {"key": "other kind", "kind": "do_ecf.deliver"},
    ]
    async with module_database.unit(None) as unit:
        for row in rows:
            await unit.connection.execute(
                insert(jobs).values(
                    **row, run_at=now, max_attempts=50, payload={}, created_at=now, updated_at=now
                )
            )
        await unit.connection.execute(text(r0003_do_ecf.WAITING))
        found = await unit.connection.execute(
            select(jobs.c.key, jobs.c.status, jobs.c.parked_reason).where(
                jobs.c.key.in_([row["key"] for row in rows])
            )
        )
        assert sorted(found.all()) == [
            ("blocked", "parked", "blocked"),
            ("other kind", "parked", "submission"),
            ("waiting", "ready", None),
        ]
