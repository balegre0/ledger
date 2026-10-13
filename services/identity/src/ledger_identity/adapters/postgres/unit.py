"""identity's unit of work over one chassis transaction: its repositories and its outbox."""

from typing import ClassVar, assert_type

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
from ledger_kernel.ids import OrganizationId, Tenant
lazy from ledger_kernel.clock import Clock
lazy from ledger_platform.database import Scope, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a transaction scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a transaction scoped to an organization")


def _organization_of(scope: Scope | None) -> OrganizationId:
    """The organization of ``scope``; exhaustive over ``Scope``, as the chassis's own reading of it is."""
    match scope:
        case None:
            raise UnscopedUnitError
        case Tenant(organization=organization):
            return organization
        case organization:
            return assert_type(organization, OrganizationId)


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> IdentityUnit:
    """The identity view of the transaction ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, which they name in their statements
    as row-level security enforces it. ``clock`` is read once: its answer is the unit's ``now`` and the time
    of every event the unit announces.
    """
    organization = _organization_of(unit.scope)
    now = clock()
    connection = unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        branches=PgBranches(connection, organization),
        emission_points=PgEmissionPoints(connection, organization),
        memberships=PgMemberships(connection, organization),
        events=OutboxEvents(unit, outbox, clock=lambda: now),
        now=now,
    )
