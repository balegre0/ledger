"""identity's unit of work over one chassis transaction: its repositories and its outbox."""

from typing import ClassVar

from ledger_identity.adapters.events import OutboxEvents
from ledger_identity.adapters.postgres.repositories import (
    PgBranches,
    PgCompanies,
    PgEmissionPoints,
    PgMemberships,
    PgOrganizations,
)
from ledger_identity.application.ports import IdentityUnit
from ledger_kernel.clock import utc_now
from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.data.database import UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a transaction scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a transaction scoped to an organization")


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> IdentityUnit:
    """The identity view of the transaction ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, and those of a company's rows to its
    company too (to none, in a unit of a whole organization), which they name in their statements as row-level
    security enforces them. ``clock`` is read once: its answer is the unit's ``now`` and the time
    of every event the unit announces.
    """
    scope = unit.scope
    if scope is None:
        raise UnscopedUnitError
    organization, company = scope.organization, scope.company
    now = clock()
    connection = unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        branches=PgBranches(connection, organization, company),
        emission_points=PgEmissionPoints(connection, organization, company),
        memberships=PgMemberships(connection, organization),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )
