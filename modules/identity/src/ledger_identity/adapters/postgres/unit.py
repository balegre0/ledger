"""identity's unit of work over a chassis unit: repositories bound to the unit's tenant, events and audit."""

from typing import ClassVar, final, override
lazy from collections.abc import Mapping
lazy from uuid import UUID

from ledger_identity.adapters.postgres.repositories import (
    PgCompanies,
    PgEstablishments,
    PgMemberships,
    PgOrganizations,
)
from ledger_identity.application.ports import Audit, Events, IdentityUnit
from ledger_kernel.errors import LedgerError
lazy from ledger_identity.application.ports import AuditAction
lazy from ledger_identity.events import IdentityEvent
lazy from ledger_platform.data import UnitOfWork


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


@final
class UnitAudit(Audit):
    """identity's audit rows, in the unit's ``audit_events``: the acting call is the actor (Decision 10)."""

    __slots__ = ("_unit",)

    def __init__(self, unit: UnitOfWork) -> None:
        self._unit = unit

    @override
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        self._unit.audit(action=action, subject=subject, before=before, after=after, detail=detail)


def bind(unit: UnitOfWork) -> IdentityUnit:
    """The identity view of ``unit``: what its use cases read, change and announce.

    The repositories are bound to the organization of the unit's scope, which they name in their statements as
    row-level security enforces it. The unit's time is the database's, read once when it began: everything it
    records and announces happened then.
    """
    scope = unit.scope
    if scope is None:
        raise UnscopedUnitError
    organization, connection = scope.organization, unit.connection
    return IdentityUnit(
        organizations=PgOrganizations(connection, organization),
        companies=PgCompanies(connection, organization),
        establishments=PgEstablishments(connection, organization),
        memberships=PgMemberships(connection, organization),
        events=UnitEvents(unit),
        audit=UnitAudit(unit),
        now=unit.now,
    )
