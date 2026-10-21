"""Revision 0002 of schema ``do_ecf``: the documents numbered from the ranges (Task 18), under RLS.

One e-CF per invoice of a company in a mode (Decision 35); a number held by one document until DGII
releases it (a partial unique index: a released number may number another document); every document of a
range of its company (a key to it). Signed exactly in the states after signing, with what the archive keeps
(keys and SHA-256, never the bytes, §6.8); a summary archived exactly for a signed document of the RFCE
route. Nothing is deleted: a document's number is accounted for for ever (§8.6.3). Every type, word and
constraint is spelled out, as revision 0001's are; the tests keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, policy

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
TENANT: Final = ("organization_id", "company_id", "mode")
SERIES: Final = (*TENANT, "environment", "ecf_type")
STATES: Final = (
    "'numbered', 'signed', 'submitted', 'in_process', 'outcome_unknown', 'accepted', "
    "'accepted_conditionally', 'rejected', 'blocked', 'voided'"
)
UNSIGNED: Final = "'numbered', 'blocked', 'voided'"
SIGNATURE: Final = " AND ".join(
    (
        f"(signed_at IS NULL) = (state IN ({UNSIGNED}))",
        "(signed_at IS NULL) = (security_code IS NULL)",
        "(signed_at IS NULL) = (qr_url IS NULL)",
        "(signed_at IS NULL) = (emission_mode IS NULL)",
        "(signed_at IS NULL) = (ecf_key IS NULL)",
        "(ecf_key IS NULL) = (ecf_sha256 IS NULL)",
    )
)
SUMMARIZED: Final = "(rfce_key IS NOT NULL) = (route = 'rfce' AND signed_at IS NOT NULL)"
RFCE: Final = f"{SUMMARIZED} AND (rfce_key IS NULL) = (rfce_sha256 IS NULL)"


def upgrade() -> None:
    """The documents' table, its keys and checks, its policies; the channel's role deletes none."""
    op.create_table(
        "documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("range_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("delivery_state", sa.Text(), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("security_code", sa.Text(), nullable=True),
        sa.Column("qr_url", sa.Text(), nullable=True),
        sa.Column("emission_mode", sa.Text(), nullable=True),
        sa.Column("ecf_key", sa.Text(), nullable=True),
        sa.Column("ecf_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("rfce_key", sa.Text(), nullable=True),
        sa.Column("rfce_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("number_fate", sa.Text(), nullable=True),
        sa.Column("draft", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*TENANT, "id", name="documents_pkey"),
        sa.UniqueConstraint(*TENANT, "invoice_id", name="documents_invoice_key"),
        sa.ForeignKeyConstraint(
            [*TENANT, "range_id"],
            [f"{S}.numbering_ranges.{column}" for column in (*TENANT, "id")],
            name="fk_documents_range",
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_documents_mode"),
        sa.CheckConstraint("environment IN ('testecf', 'certecf', 'ecf')", name="ck_documents_environment"),
        sa.CheckConstraint(
            "ecf_type IN ('31', '32', '33', '34', '41', '43', '44', '45', '46', '47')",
            name="ck_documents_ecf_type",
        ),
        sa.CheckConstraint("(environment = 'ecf') = (mode = 'live')", name="ck_documents_live"),
        sa.CheckConstraint(f"state IN ({STATES})", name="ck_documents_state"),
        sa.CheckConstraint("route IN ('reception', 'rfce')", name="ck_documents_route"),
        sa.CheckConstraint(
            "delivery_state IN ('not_started', 'not_applicable', 'delivering', 'delivered', 'failed')",
            name="ck_documents_delivery_state",
        ),
        sa.CheckConstraint("emission_mode IN ('normal')", name="ck_documents_emission_mode"),
        sa.CheckConstraint("number_fate IN ('used', 'burned', 'released')", name="ck_documents_number_fate"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_documents_sequence"),
        sa.CheckConstraint(SIGNATURE, name="ck_documents_signature"),
        sa.CheckConstraint(RFCE, name="ck_documents_rfce"),
        sa.CheckConstraint(
            "octet_length(ecf_sha256) = 32 AND octet_length(rfce_sha256) = 32", name="ck_documents_digests"
        ),
        sa.CheckConstraint("version > 0", name="ck_documents_version"),
        schema=S,
    )
    op.create_index(
        "documents_number",
        "documents",
        [*SERIES, "sequence"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("number_fate IS DISTINCT FROM 'released'"),
    )
    for statement in policy(f"{S}.documents", RlsScope.TENANT):
        op.execute(statement)
    op.execute(f'REVOKE DELETE ON "{S}".documents FROM "{S}_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
