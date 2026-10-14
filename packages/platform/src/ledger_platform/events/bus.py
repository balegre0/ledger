"""The ``EventBus`` port (§3.5, §3.9): the outbox today; an extracted module gets a broker adapter."""

from typing import Protocol, final, override

lazy from ledger_kernel.events import Event
lazy from ledger_platform.data.unit_of_work import UnitOfWork


class EventBus(Protocol):
    """Publishes an event as part of a unit's transaction."""

    def publish(self, unit: UnitOfWork, event: Event) -> None:
        """Make ``event`` part of ``unit``: it is announced only if the unit commits."""


@final
class PostgresEventBus(EventBus):
    """The outbox in the unit's own schema."""

    @override
    def publish(self, unit: UnitOfWork, event: Event) -> None:
        unit.publish(event)
