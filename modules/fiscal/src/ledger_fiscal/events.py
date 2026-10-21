"""What fiscal announces (§3.5): an enrollment changed (public ``enrollment.updated``, for webhooks).

Scoped to the enrollment's company in its mode; its subject is the enrollment, its ``aggregate_version`` the
version the change left. The status travels as its word: this module sits below the domain.
"""

from dataclasses import dataclass
from typing import ClassVar, final
lazy from uuid import UUID

from ledger_kernel.events import Event


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EnrollmentChanged(Event):
    """An enrollment was created, reviewed into another state, or disabled."""

    TYPE: ClassVar[str] = "fiscal.enrollment.changed"
    VERSION: ClassVar[int] = 1
    enrollment: UUID
    channel: str
    environment: str
    status: str
    """``pending``, ``active`` or ``disabled``."""


type FiscalEvent = EnrollmentChanged
"""Every event fiscal publishes (its manifest's ``[[events.publishes]]``)."""
