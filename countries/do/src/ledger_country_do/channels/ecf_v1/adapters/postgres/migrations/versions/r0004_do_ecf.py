"""Revision 0004 of schema ``do_ecf``: the receiver (Task 22): documents received, its seeds and tokens.

``received_documents`` holds the e-CF other emitters sent a company and the receiver received, each once per
environment, issuer and e-NCF (Decision 16), under RLS, with the keys and SHA-256 of what the archive keeps
(the document as it arrived, the receipt it was answered with); the channel's role deletes none.

``receiver_seeds`` and ``receiver_tokens`` are outside row-level security by design (§6.3): the receiver
finds a seed or a token by its SHA-256 before it knows a tenant, and each names the company and environment
it serves. Neither keeps the secret itself. A token is never changed; both are deleted once expired
(``do_ecf.purge_receiver_credentials``).

``documents`` gains its buyer's commercial approval (``commercial_approval``, ``{approved, reason, at}``).
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, policy

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
TENANT: Final = ("organization_id", "company_id", "mode")
ENVIRONMENTS: Final = "environment IN ('testecf', 'certecf', 'ecf')"


def upgrade() -> None:
    """The received documents' table and policies, the receiver's credentials, the documents' approval."""
    op.add_column("documents", sa.Column("commercial_approval", postgresql.JSONB(), nullable=True), schema=S)
    op.create_table(
        "received_documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("issuer_rnc", sa.Text(), nullable=False),
        sa.Column("issuer_name", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("buyer_rnc", sa.Text(), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("itbis", sa.Numeric(18, 2), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_code", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("xml_key", sa.Text(), nullable=False),
        sa.Column("xml_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("arecf_key", sa.Text(), nullable=False),
        sa.Column("arecf_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(*TENANT, "id", name="received_documents_pkey"),
        sa.UniqueConstraint(
            *TENANT, "environment", "issuer_rnc", "ecf_type", "sequence", name="received_documents_number_key"
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_received_documents_mode"),
        sa.CheckConstraint(ENVIRONMENTS, name="ck_received_documents_environment"),
        sa.CheckConstraint(
            "ecf_type IN ('31', '32', '33', '34', '41', '43', '44', '45', '46', '47')",
            name="ck_received_documents_ecf_type",
        ),
        sa.CheckConstraint("(environment = 'ecf') = (mode = 'live')", name="ck_received_documents_live"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_received_documents_sequence"),
        sa.CheckConstraint(
            "octet_length(xml_sha256) = 32 AND octet_length(arecf_sha256) = 32",
            name="ck_received_documents_digests",
        ),
        sa.CheckConstraint("version > 0", name="ck_received_documents_version"),
        schema=S,
    )
    for statement in policy(f"{S}.received_documents", RlsScope.TENANT):
        op.execute(statement)
    op.execute(f'REVOKE DELETE ON "{S}".received_documents FROM "{S}_rw"')
    op.create_table(
        "receiver_seeds",
        sa.Column("seed_hash", sa.LargeBinary(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("seed_hash", name="receiver_seeds_pkey"),
        sa.CheckConstraint(ENVIRONMENTS, name="ck_receiver_seeds_environment"),
        sa.CheckConstraint("octet_length(seed_hash) = 32", name="ck_receiver_seeds_hash"),
        schema=S,
    )
    op.create_index("receiver_seeds_expiry", "receiver_seeds", ["expires_at"], schema=S)
    op.create_table(
        "receiver_tokens",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("senders", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("stand_in", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name="receiver_tokens_pkey"),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_receiver_tokens_mode"),
        sa.CheckConstraint(ENVIRONMENTS, name="ck_receiver_tokens_environment"),
        sa.CheckConstraint("(environment = 'ecf') = (mode = 'live')", name="ck_receiver_tokens_live"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="ck_receiver_tokens_hash"),
        sa.CheckConstraint("NOT stand_in OR environment <> 'ecf'", name="ck_receiver_tokens_stand_in"),
        schema=S,
    )
    op.create_index("receiver_tokens_expiry", "receiver_tokens", ["expires_at"], schema=S)
    op.execute(f'REVOKE UPDATE ON "{S}".receiver_tokens FROM "{S}_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
