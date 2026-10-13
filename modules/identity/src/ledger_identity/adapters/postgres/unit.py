"""identity's unit of work over a chassis unit: repositories bound to the unit's tenant, and its events."""

from typing import ClassVar, final, override

from ledger_identity.adapters.postgres.repositories import (
    PgBranches,
    PgCompanies,
    PgEmissionPoints,
    PgMemberships,
    PgOrganizations,
)
from ledger_identity.application.ports import Events, IdentityUnit
from ledger_kernel.errors import LedgerError
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_platform.data.unit_of_work import UnitOfWork


class UnscopedUnitError(LedgerError):
    """identity's state was to be read in a unit scoped to nobody: a defect of the caller."""

    code: ClassVar[str] = "unscoped_unit"

    def __init__(self) -> None:
        super().__init__("identity's state is read and changed in a unit scoped to an organization")


@final
class UnitEvents(Events):
    """identity's events, in the unit's outbox: they commit with the change or not at all (rule 3)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def publish(self, event: IdentityEvent) -> None:
        self._unit.publish(event)


def bind(unit: UnitOfWork) -> IdentityUnit:
    """The identity view of ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, and those of a company's rows to its
    company too (to none, in a unit of a whole organization), which they name in their statements as row-level
    security enforces them. The unit's time is the database's, read once when it began: everything it
    records and announces happened then.
    """
    scope = unit.scope
    if scope is None:
        raise UnscopedUnitError
    organization, company, connection = scope.organization, scope.company, unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        branches=PgBranches(connection, organization, company),
        emission_points=PgEmissionPoints(connection, organization, company),
        memberships=PgMemberships(connection, organization),
        events=UnitEvents(unit),
        now=unit.now,
    )
