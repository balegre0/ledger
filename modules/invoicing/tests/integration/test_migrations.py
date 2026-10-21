"""invoicing's schema as ``ledger-migrate`` makes it: at head, equal to the models, under row-level security.

How migrations run (the lock, the owner role, readiness) is the platform's, tested there; here the worker's
template database holds invoicing's schema at head, as the migrate app brought it there.
"""

import re
from typing import Final

from sqlalchemy import CheckConstraint

from ledger_invoicing.adapters.postgres.schema import MIGRATIONS
from ledger_invoicing.adapters.postgres.tables import SCHEMA, InvoicingTable
from ledger_invoicing.domain.fiscal import FiscalStatusName
from ledger_invoicing.domain.invoice import InvoiceStatus
from ledger_kernel.regimes import Direction, DocumentKind, TaxCategoryKind
from ledger_kernel.tenancy import Mode
from ledger_platform.data.tables import chassis_tables
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
lazy from ledger_testing.postgres import WorkerDatabase

DOCUMENT_TABLES: Final = frozenset(
    {"invoices", "invoice_lines", "invoice_line_taxes", "invoice_taxes", "fiscal_statuses"}
)
CHASSIS_TABLES: Final = frozenset(
    {"outbox", "inbox", "jobs", "subscriptions", "idempotency_keys", "audit_events"}
)
MODELS: Final = combined(chassis_tables(SCHEMA).outbox.metadata, InvoicingTable.metadata)
"""The chassis's tables of schema ``invoicing`` and invoicing's own, as its revision must make them."""
QUOTED: Final = re.compile(r"'([^']*)'")
"""A literal in a constraint's definition as PostgreSQL prints it back: ``'draft'::character varying``."""
COMMANDS: Final = ("delete", "insert", "select", "update")


def superuser(worker: WorkerDatabase) -> str:
    """The worker's database as the superuser (SQLAlchemy URL): what the catalog readers connect as."""
    return worker.url(role=worker.server.user, password=worker.server.password)


def test_the_schema_holds_the_chassis_s_tables_and_a_document_s_at_one_revision(
    migrated: WorkerDatabase,
) -> None:
    assert MIGRATIONS.head == "0002"  # 0001: the chassis and a document; 0002: the fiscal mirror
    assert tables_of(superuser(migrated), schema=SCHEMA) == {
        *DOCUMENT_TABLES,
        *CHASSIS_TABLES,
        "alembic_version",
    }


def test_the_migrated_schema_is_exactly_the_models(migrated: WorkerDatabase) -> None:
    assert pending_changes(superuser(migrated), MODELS, schema=SCHEMA) is None


def test_the_check_constraints_are_the_models_and_allow_exactly_the_domains_words(
    migrated: WorkerDatabase,
) -> None:
    """Alembic does not compare check constraints: their names and the words they allow are compared here."""
    checks = check_constraints(superuser(migrated), schema=SCHEMA)
    declared = {
        str(constraint.name)
        for table in InvoicingTable.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert declared <= set(checks)  # beside the chassis's own, which its tests pin
    for name, words in {
        "ck_invoices_mode": Mode,
        "ck_invoices_kind": DocumentKind,
        "ck_invoices_direction": Direction,
        "ck_invoices_status": InvoiceStatus,
        "ck_invoice_line_taxes_kind": TaxCategoryKind,
        "ck_fiscal_statuses_mode": Mode,
        "ck_fiscal_statuses_status": FiscalStatusName,
    }.items():
        assert set(QUOTED.findall(checks[name])) == {str(word) for word in words}, name


def test_every_table_of_a_document_is_under_its_tenant_s_policies_forced_on_its_owner(
    migrated: WorkerDatabase,
) -> None:
    url = superuser(migrated)
    found = policies(url, schema=SCHEMA)
    for owner in DOCUMENT_TABLES:
        assert [policy for name, policy in found if name == owner] == [
            f"{owner}_{command}" for command in COMMANDS
        ]
    assert row_security(url, schema=SCHEMA) == {
        **dict.fromkeys(DOCUMENT_TABLES, (True, True)),
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
    assert primary_keys(superuser(migrated), schema=SCHEMA) == {
        "alembic_version": ["version_num"],
        **{
            table.name: [column.name for column in table.primary_key.columns]
            for table in MODELS.tables.values()
        },
    }


def test_a_business_key_is_unique_per_tenant_and_type_and_every_index_leads_with_the_tenant(
    migrated: WorkerDatabase,
) -> None:
    assert indexes_of(superuser(migrated), "invoices", schema=SCHEMA) == {
        "invoices_pkey": "CREATE UNIQUE INDEX invoices_pkey ON invoicing.invoices USING btree (id)",
        "uq_invoices_tenant": (
            "CREATE UNIQUE INDEX uq_invoices_tenant ON invoicing.invoices "
            "USING btree (organization_id, company_id, mode, id)"
        ),
        "uq_invoices_external_reference": (
            "CREATE UNIQUE INDEX uq_invoices_external_reference ON invoicing.invoices "
            "USING btree (organization_id, company_id, mode, kind, external_reference) "
            "WHERE (external_reference IS NOT NULL)"
        ),
        "ix_invoices_corrects": (
            "CREATE INDEX ix_invoices_corrects ON invoicing.invoices "
            "USING btree (organization_id, company_id, mode, corrects) WHERE (corrects IS NOT NULL)"
        ),
    }


def test_the_mirror_is_keyed_by_its_tenant_document_and_channel(migrated: WorkerDatabase) -> None:
    assert indexes_of(superuser(migrated), "fiscal_statuses", schema=SCHEMA) == {
        "fiscal_statuses_pkey": (
            "CREATE UNIQUE INDEX fiscal_statuses_pkey ON invoicing.fiscal_statuses "
            "USING btree (organization_id, company_id, mode, invoice_id, channel)"
        ),
    }
