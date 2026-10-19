"""Revision 0003 of identity's schema: API clients are the organization's rows, whatever their mode.

A client's mode is what its credential is bound to, not a partition of the organization's rows: how a machine
stands is found by its subject across both modes, in the one transaction that also reads its organization's
pin (``adapters.standings``). Every other read and write names its mode itself (``PgApiClients``). The
``moded_organization`` policies give way to the ``organization`` ones (``rls.policy``); the table, its index
of one active client per machine user across modes, and its rows stay as they are.
"""

lazy from collections.abc import Sequence

from alembic import op

from ledger_identity.adapters.postgres.tables import SCHEMA
from ledger_platform.data.rls import RlsScope, policy

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The organization's policies on ``api_clients`` in place of the moded organization's."""
    for command in ("select", "insert", "update", "delete"):
        op.execute(f'DROP POLICY "api_clients_{command}" ON "{SCHEMA}".api_clients')
    for statement in policy(f"{SCHEMA}.api_clients", RlsScope.ORGANIZATION):
        op.execute(statement)


def downgrade() -> None:
    """Revisions are expand-only (§6.5): nothing goes back past 0001 but a new database."""
