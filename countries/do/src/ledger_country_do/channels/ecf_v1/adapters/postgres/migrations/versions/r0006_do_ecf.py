"""Revision 0006 of schema ``do_ecf``: a document's printed representation (Task 24, spec §8.6.7).

``documents`` gains where the archive keeps the PDF first rendered of it (``representation_key``) and its
SHA-256: later renders answer those bytes (Decision 12). A check keeps them whole (both or neither, a SHA-256
of 32 bytes) and on a signed document only.
"""

from typing import Final
lazy from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S: Final = "do_ecf"
REPRESENTATION: Final = (
    "(representation_key IS NULL) = (representation_sha256 IS NULL) "
    "AND (representation_sha256 IS NULL OR octet_length(representation_sha256) = 32) "
    "AND (representation_key IS NULL OR signed_at IS NOT NULL)"
)


def upgrade() -> None:
    """The documents' representation columns and their check."""
    op.add_column("documents", sa.Column("representation_key", sa.Text()), schema=S)
    op.add_column("documents", sa.Column("representation_sha256", sa.LargeBinary()), schema=S)
    op.create_check_constraint("ck_documents_representation", "documents", REPRESENTATION, schema=S)


def downgrade() -> None:
    """Expand-only (§6.5): a schema goes back only by restoring a backup."""
