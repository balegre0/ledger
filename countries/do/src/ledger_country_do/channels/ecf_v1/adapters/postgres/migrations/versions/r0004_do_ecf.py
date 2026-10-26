"""Revision 0004 of schema ``do_ecf``: an accepted e-CF's delivery to its buyer (Task 21); company options.

``documents`` gains its delivery's progress: when it started (``delivery_since``: its seven days run from
there, Decision 21), how many attempts failed, why it failed or does not apply, and the key and SHA-256 of
the buyer's receipt (trusted, or refused and kept as evidence). Checks keep them coherent: a receipt by both
or neither, a delivery under way with its start.

``company_options`` holds how a company delivers to its buyers in a mode (``buyer_authentication``, off when
there is no row, Decision 19), under the tenant's policy; set, never deleted.

Every document DGII accepted (or accepted conditionally) through its reception before this revision, its
delivery ``not_started``, gets its ``do_ecf.deliver`` job (Task 19's review): ready now, the kind's limits
(50 attempts, 8 days), a job of that key already there left as it is. The documents of every company are
read by the owner with row-level security lifted for that statement alone (``SWEEP``).
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ledger_platform.data import RlsScope, policy

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
TENANT: Final = ("organization_id", "company_id", "mode")
RECEIPT: Final = (
    "(receipt_key IS NULL) = (receipt_sha256 IS NULL) "
    "AND (receipt_sha256 IS NULL OR octet_length(receipt_sha256) = 32)"
)
DELIVERING: Final = "delivery_state <> 'delivering' OR delivery_since IS NOT NULL"
UNDELIVERED: Final = (
    "INSERT INTO do_ecf.jobs (kind, key, organization_id, company_id, mode, status, run_at, attempts, "
    "max_attempts, deadline_at, payload, created_at, updated_at) "
    "SELECT 'do_ecf.deliver', id::text, organization_id, company_id, mode, 'ready', now(), 0, 50, "
    "now() + interval '8 days', '{}'::jsonb, now(), now() FROM do_ecf.documents "
    "WHERE state IN ('accepted', 'accepted_conditionally') AND route = 'reception' "
    "AND delivery_state = 'not_started' "
    "ON CONFLICT (kind, key) DO NOTHING"
)
SWEEP: Final = (
    "ALTER TABLE do_ecf.documents NO FORCE ROW LEVEL SECURITY",
    UNDELIVERED,
    "ALTER TABLE do_ecf.documents FORCE ROW LEVEL SECURITY",
)
"""The sweep, as the schema's owner runs it: row-level security is forced on the owner too, so the documents
of every company are read with it lifted for that one statement, inside the revision's transaction."""


def upgrade() -> None:
    """The documents' delivery columns and checks, the companies' options; undelivered documents enqueued."""
    op.add_column("documents", sa.Column("delivery_since", sa.DateTime(timezone=True)), schema=S)
    op.add_column(
        "documents",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=S,
    )
    op.add_column("documents", sa.Column("delivery_reason", sa.Text()), schema=S)
    op.add_column("documents", sa.Column("receipt_key", sa.Text()), schema=S)
    op.add_column("documents", sa.Column("receipt_sha256", sa.LargeBinary()), schema=S)
    for name, condition in (
        ("ck_documents_receipt", RECEIPT),
        ("ck_documents_delivering", DELIVERING),
        ("ck_documents_delivery_attempts", "delivery_attempts >= 0"),
    ):
        op.create_check_constraint(name, "documents", condition, schema=S)
    op.create_table(
        "company_options",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("buyer_authentication", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*TENANT, name="company_options_pkey"),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_company_options_mode"),
        sa.CheckConstraint("version > 0", name="ck_company_options_version"),
        schema=S,
    )
    for statement in policy(f"{S}.company_options", RlsScope.TENANT):
        op.execute(statement)
    op.execute(f'REVOKE DELETE ON "{S}".company_options FROM "{S}_rw"')
    for statement in SWEEP:
        op.execute(statement)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
