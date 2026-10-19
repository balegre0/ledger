"""Revision 0001 of schema ``signer``: the chassis's tables (both versions), then signer's, under RLS.

Its role is granted no more than the repositories need.
``certificates`` holds a certificate's public part, ``custody`` its sealed material; both are a company's
(scope ``company``: a certificate is shared by its company's modes). A certificate is retired, never deleted
(``signer_rw`` loses ``DELETE`` on it); its sealed material is written once and deleted when it is retired
(``signer_rw`` loses ``UPDATE`` on it). Every type, word and constraint is spelled out (a revision records
what was done and must not move with the models); the tests keep the models equal to it.
"""

from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

import sqlalchemy as sa
from alembic import op

from ledger_platform.data import RlsScope, chassis_schema_v1, chassis_schema_v2, policy

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "signer"


def _moment(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _active_in(mode: str) -> None:
    op.create_index(
        f"certificates_one_active_{mode}",
        "certificates",
        ["company_id"],
        unique=True,
        schema=S,
        postgresql_where=sa.text(f"status = 'active' AND serves_{mode}"),
    )


def upgrade() -> None:
    """The chassis's tables, signer's two, one active certificate per company and mode, policies, grants."""
    chassis_schema_v1(op, S)
    chassis_schema_v2(op, S)
    op.create_table(
        "certificates",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serves_test", sa.Boolean(), nullable=False),
        sa.Column("serves_live", sa.Boolean(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("certificate_der", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("serial_number", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        _moment("not_before"),
        _moment("not_after"),
        _moment("installed_at"),
        _moment("retired_at", nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "company_id", "id", name="certificates_pkey"),
        sa.CheckConstraint("serves_test OR serves_live OR status = 'retired'", name="ck_certificates_modes"),
        sa.CheckConstraint("backend IN ('local', 'remote')", name="ck_certificates_backend"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_certificates_status"),
        sa.CheckConstraint("octet_length(fingerprint_sha256) = 32", name="ck_certificates_fingerprint"),
        sa.CheckConstraint("not_after > not_before", name="ck_certificates_validity"),
        schema=S,
    )
    _active_in("test")
    _active_in("live")
    op.create_table(
        "custody",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("certificate_id", sa.Uuid(), nullable=False),
        sa.Column("sealed", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("kms_key_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "company_id", "certificate_id", name="custody_pkey"),
        sa.ForeignKeyConstraint(
            ["organization_id", "company_id", "certificate_id"],
            [f"{S}.certificates.organization_id", f"{S}.certificates.company_id", f"{S}.certificates.id"],
            name="fk_custody_certificate",
        ),
        schema=S,
    )
    for name in ("certificates", "custody"):
        for statement in policy(f"{S}.{name}", RlsScope.COMPANY):
            op.execute(statement)
    op.execute('REVOKE DELETE ON "signer".certificates FROM "signer_rw"')
    op.execute('REVOKE UPDATE ON "signer".custody FROM "signer_rw"')


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
