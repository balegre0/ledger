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
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_platform.data.database import Scope, UnitOfWork
lazy from ledger_platform.outbox import Outbox


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a transaction scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a transaction scoped to an organization")


def _tenancy_of(scope: Scope | None) -> tuple[OrganizationId, CompanyId | None]:
    """The organization of ``scope``, and its company if it is one's; exhaustive over ``Scope``."""
    match scope:
        case None:
            raise UnscopedUnitError
        case Tenant(organization=organization, company=company):
            return organization, company
        case organization:
            return assert_type(organization, OrganizationId), None


def bind(unit: UnitOfWork, outbox: Outbox, *, clock: Clock = utc_now) -> IdentityUnit:
    """The identity view of the transaction ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, and those of a company's rows to its
    company too (to none, in a unit of a whole organization), which they name in their statements as row-level
    security enforces them. ``clock`` is read once: its answer is the unit's ``now`` and the time
    of every event the unit announces.
    """
    organization, company = _tenancy_of(unit.scope)
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
