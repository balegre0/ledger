"""Revision 0003 of schema ``do_ecf``: a document's way through DGII (Task 19), and what DGII gave it.

``documents`` gains the submission's mark (``submission_started_at``, committed before the one call that
sends it: a document marked is never sent again, Decision 8), DGII's track id, when its outcome became
unknown (``track_id_since``: the track-id window runs from there, Decision 7), how often DGII was asked
(``polls``), DGII's last code, ``secuenciaUtilizada`` and messages. Checks keep them coherent: a mark only
once signed and on every document past a submission, a track id on every document DGII is deliberating
about, a moment on every outcome unknown, a number's fate exactly once DGII decided (used when accepted,
burned or released when rejected).

``submissions`` lists every track id a document was given or adopted, by its number too: a track id of an
earlier document with the same e-NCF (a released number taken again) is never adopted (Decision 9). Nothing
in it is changed or deleted.

Jobs Task 18's handler parked ``submission`` (a signed document waiting for this step) are made ready.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ledger_platform.data import RlsScope, policy

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
TENANT: Final = ("organization_id", "company_id", "mode")
SERIES: Final = (*TENANT, "environment", "ecf_type")
UNSIGNED: Final = "'numbered', 'blocked', 'voided'"
SUBMITTED: Final = (
    "'submitted', 'in_process', 'outcome_unknown', 'accepted', 'accepted_conditionally', 'rejected'"
)
DECIDED: Final = "'accepted', 'accepted_conditionally', 'rejected'"
SUBMISSION: Final = (
    f"(state IN ({UNSIGNED}) AND submission_started_at IS NULL) OR state = 'signed' "
    f"OR (state IN ({SUBMITTED}) AND submission_started_at IS NOT NULL)"
)
TRACKED: Final = "state NOT IN ('submitted', 'in_process') OR track_id IS NOT NULL"
UNKNOWN: Final = "state <> 'outcome_unknown' OR track_id_since IS NOT NULL"
FATE: Final = (
    f"(number_fate IS NOT NULL) = (state IN ({DECIDED})) "
    "AND (state <> 'rejected' OR number_fate IN ('burned', 'released')) "
    "AND (state NOT IN ('accepted', 'accepted_conditionally') OR number_fate = 'used')"
)
WAITING: Final = (
    "UPDATE do_ecf.jobs SET status = 'ready', run_at = now(), parked_reason = NULL, updated_at = now() "
    "WHERE kind = 'do_ecf.advance' AND status = 'parked' AND parked_reason = 'submission'"
)


def upgrade() -> None:
    """The documents' new columns and checks, the submissions' table, its policies; waiting jobs ready."""
    op.add_column("documents", sa.Column("submission_started_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("documents", sa.Column("track_id", sa.Text()), schema=S)
    op.add_column("documents", sa.Column("track_id_since", sa.DateTime(timezone=True)), schema=S)
    op.add_column(
        "documents", sa.Column("polls", sa.Integer(), nullable=False, server_default=sa.text("0")), schema=S
    )
    op.add_column("documents", sa.Column("outcome_code", sa.Integer()), schema=S)
    op.add_column("documents", sa.Column("sequence_used", sa.Boolean()), schema=S)
    op.add_column(
        "documents",
        sa.Column("messages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema=S,
    )
    for name, condition in (
        ("ck_documents_submission", SUBMISSION),
        ("ck_documents_track_id", TRACKED),
        ("ck_documents_unknown", UNKNOWN),
        ("ck_documents_fate", FATE),
        ("ck_documents_polls", "polls >= 0"),
    ):
        op.create_check_constraint(name, "documents", condition, schema=S)
    op.create_table(
        "submissions",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("ecf_type", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(*TENANT, "document_id", "track_id", name="submissions_pkey"),
        sa.ForeignKeyConstraint(
            [*TENANT, "document_id"],
            [f"{S}.documents.{column}" for column in (*TENANT, "id")],
            name="fk_submissions_document",
        ),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_submissions_mode"),
        sa.CheckConstraint("environment IN ('testecf', 'certecf', 'ecf')", name="ck_submissions_environment"),
        sa.CheckConstraint(
            "ecf_type IN ('31', '32', '33', '34', '41', '43', '44', '45', '46', '47')",
            name="ck_submissions_ecf_type",
        ),
        sa.CheckConstraint("(environment = 'ecf') = (mode = 'live')", name="ck_submissions_live"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 9999999999", name="ck_submissions_sequence"),
        sa.CheckConstraint("length(track_id) > 0", name="ck_submissions_track_id"),
        schema=S,
    )
    op.create_index("submissions_number", "submissions", [*SERIES, "sequence"], schema=S)
    for statement in policy(f"{S}.submissions", RlsScope.TENANT):
        op.execute(statement)
    op.execute(f'REVOKE UPDATE, DELETE ON "{S}".submissions FROM "{S}_rw"')
    op.execute(WAITING)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
