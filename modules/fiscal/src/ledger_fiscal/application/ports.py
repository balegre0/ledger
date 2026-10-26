"""What the use cases need of the world: fiscal's state, read and changed inside one unit of work.

A unit is one transaction scoped to a company in a mode: row-level security limits what its repository sees
to that tenant, and the repository names the tenant in every statement too (tenancy is checked twice, §2).
The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Literal, Protocol, final
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime
lazy from uuid import UUID

lazy from ledger_fiscal.domain.enrollments import Enrollment, EnrollmentId
lazy from ledger_fiscal.domain.routing import Routing
lazy from ledger_fiscal.events import FiscalEvent
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Tenant


class Enrollments(Protocol, metaclass=ABCMeta):
    """The enrollments of the unit's company in the unit's mode."""

    @abstractmethod
    async def add(self, enrollment: Enrollment) -> EnrollmentId | None:
        """Keep a new enrollment; ``None`` once kept, else the one not disabled that holds its channel."""

    @abstractmethod
    async def get(self, enrollment: EnrollmentId) -> Enrollment | None:
        """The enrollment, or ``None``."""

    @abstractmethod
    async def locked(self, enrollment: EnrollmentId) -> Enrollment | None:
        """The enrollment, locked until the unit ends (a review or a disabling waits for the others)."""

    @abstractmethod
    async def update(self, enrollment: Enrollment, *, version: int) -> None:
        """Replace the enrollment ``locked`` read at ``version`` by ``enrollment`` (its lock keeps it so)."""

    @abstractmethod
    async def on(self, channel: str, environment: str) -> Enrollment | None:
        """The enrollment not disabled on ``channel`` in ``environment``, or ``None``."""

    @abstractmethod
    async def every(self) -> tuple[Enrollment, ...]:
        """Every enrollment, disabled ones too, in id order."""

    @abstractmethod
    async def page(self, request: PageRequest) -> Page[Enrollment]:
        """The enrollments, disabled ones too, in id order."""

    @abstractmethod
    async def mark_due(self, channel: str) -> None:
        """Mark the enrollments on ``channel`` not disabled ``review_due`` (Decision 34): one statement."""


class Routings(Protocol, metaclass=ABCMeta):
    """The documents of the unit's company in the unit's mode routed to channels (``fiscal.routings``)."""

    @abstractmethod
    async def add(self, routing: Routing) -> Routing:
        """Keep ``routing``; the one kept first for its document and channel when there is one already."""

    @abstractmethod
    async def of(self, invoice: UUID) -> tuple[Routing, ...]:
        """Every routing of ``invoice``, by channel."""

    @abstractmethod
    async def get(self, invoice: UUID, channel: str) -> Routing | None:
        """The routing of ``invoice`` to ``channel``, or ``None``."""


class Jobs(Protocol, metaclass=ABCMeta):
    """fiscal's own jobs, enqueued and woken with the unit (``fiscal.route``)."""

    @abstractmethod
    def route_later(self, invoice: UUID, channel: str) -> None:
        """Route ``invoice`` to ``channel`` again in a job: its channel had no number to give it now."""

    @abstractmethod
    def wake_routes(self) -> None:
        """Ready the company's routings parked for a number: what its channels require changed."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces once it commits."""

    @abstractmethod
    def publish(self, event: FiscalEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox, written with the unit)."""


type AuditAction = Literal["enrollment.enroll", "enrollment.review", "enrollment.disable"]
"""What a use case records it did; the part before the dot names the kind of subject."""


class Audit(Protocol, metaclass=ABCMeta):
    """Who changed what, recorded with the change (``audit_events``, §6.4); the actor is the unit's call."""

    @abstractmethod
    def record(self, action: AuditAction, subject: UUID, *, before: object, after: object) -> None:
        """Record that ``action`` changed ``subject`` from ``before`` to ``after`` (``None``: not there)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalUnit:
    """One unit of work: fiscal's state as one transaction sees it, and what it announces."""

    tenant: Tenant
    """The company in its mode the unit is scoped to."""
    enrollments: Enrollments
    routings: Routings
    jobs: Jobs
    events: Events
    audit: Audit
    now: datetime
    """The unit's time: everything it records and announces happened then."""


class FiscalUnits(Protocol, metaclass=ABCMeta):
    """Where the use cases get the units they work in."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[FiscalUnit]:
        """A unit scoped to ``tenant`` (a company in a mode); it commits when its block ends."""
