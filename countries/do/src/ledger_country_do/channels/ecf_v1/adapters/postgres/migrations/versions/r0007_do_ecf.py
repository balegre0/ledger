"""Revision 0007 of schema ``do_ecf``: a company's commercial approval of the e-CF it received (Task 23).

``received_documents`` gains the company's decision (``approval_decision``, approved or rejected, and why it
rejected), when it was asked for (``approval_requested_at``: the seven days of Decision 21 run from there),
how many attempts failed since, the ACECF signed for it (its moment, ``FechaHoraAprobacionComercial``, and
the key and SHA-256 of what the archive keeps), and where sending it to each party stands: the emitter
(``approval_emitter``, why, ``approval_sent_to_emitter_at``) and DGII (``approval_dgii``, why, DGII's
messages, the submission's mark ``approval_dgii_started_at``, ``approval_sent_to_dgii_at``). Checks keep them
coherent: a decision with its moment and both parties' states, or none of them; a reason exactly when it
rejects; an ACECF by its moment, key and SHA-256, all or none; a party marked or sent only once the ACECF is
signed. The channel's role keeps its UPDATE on the table (Task 22) and still deletes nothing.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
T: Final = "received_documents"
SENDING: Final = "'pending', 'sent', 'refused', 'not_applicable', 'outcome_unknown', 'failed'"
APPROVAL: Final = (
    "(approval_decision IS NULL) = (approval_requested_at IS NULL) "
    "AND (approval_decision IS NULL) = (approval_emitter IS NULL) "
    "AND (approval_decision IS NULL) = (approval_dgii IS NULL)"
)
ACECF: Final = (
    "(approval_key IS NULL) = (approval_sha256 IS NULL) "
    "AND (approval_key IS NULL) = (approval_at IS NULL) "
    "AND (approval_sha256 IS NULL OR octet_length(approval_sha256) = 32)"
)
SENT: Final = (
    "(approval_emitter IS DISTINCT FROM 'sent' OR approval_sent_to_emitter_at IS NOT NULL) "
    "AND (approval_dgii IS DISTINCT FROM 'sent' OR approval_sent_to_dgii_at IS NOT NULL) "
    "AND (approval_key IS NOT NULL OR (approval_dgii_started_at IS NULL "
    "AND approval_sent_to_emitter_at IS NULL AND approval_sent_to_dgii_at IS NULL))"
)
CHECKS: Final = (
    ("ck_received_documents_approval_decision", "approval_decision IN ('approved', 'rejected')"),
    ("ck_received_documents_approval_emitter", f"approval_emitter IN ({SENDING})"),
    ("ck_received_documents_approval_dgii", f"approval_dgii IN ({SENDING})"),
    ("ck_received_documents_approval", APPROVAL),
    (
        "ck_received_documents_approval_reason",
        "(approval_decision IS NOT DISTINCT FROM 'rejected') = (approval_reason IS NOT NULL)",
    ),
    ("ck_received_documents_approval_acecf", ACECF),
    ("ck_received_documents_approval_sent", SENT),
    ("ck_received_documents_approval_attempts", "approval_attempts >= 0"),
)


def upgrade() -> None:
    """The received documents' approval columns and their checks."""
    timestamp = sa.DateTime(timezone=True)
    for column in (
        sa.Column("approval_decision", sa.Text()),
        sa.Column("approval_reason", sa.Text()),
        sa.Column("approval_requested_at", timestamp),
        sa.Column("approval_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("approval_at", timestamp),
        sa.Column("approval_key", sa.Text()),
        sa.Column("approval_sha256", sa.LargeBinary()),
        sa.Column("approval_emitter", sa.Text()),
        sa.Column("approval_emitter_reason", sa.Text()),
        sa.Column("approval_sent_to_emitter_at", timestamp),
        sa.Column("approval_dgii", sa.Text()),
        sa.Column("approval_dgii_reason", sa.Text()),
        sa.Column(
            "approval_dgii_messages",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("approval_dgii_started_at", timestamp),
        sa.Column("approval_sent_to_dgii_at", timestamp),
    ):
        op.add_column(T, column, schema=S)
    for name, condition in CHECKS:
        op.create_check_constraint(name, T, condition, schema=S)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
