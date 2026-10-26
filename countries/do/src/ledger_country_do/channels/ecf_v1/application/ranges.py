"""A company's DGII-authorized ranges (§8.6.3): registered, listed, read; and the ports their use cases need.

A range is registered in one of the channel's environments, in the mode that environment serves (a live
range numbers live documents only, a test one never does), for an environment the company is enrolled in
(fiscal's enrollments, asked outside any unit). In the unit that registers it, the series' row is locked
(``Ranges.series``), so two registrations of a series, or a registration and an allocation, never interleave;
the range is checked against its series (``numbering.authorized``), kept, audited, fiscal is told that
what the channel requires of the company changed, and the company's jobs waiting for a range are woken.

A unit is one transaction scoped to a company in a mode: row-level security limits what its repositories see
to that tenant, and they name the tenant in every statement too (tenancy is checked twice, §2). The ports'
methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, final
lazy from collections.abc import Mapping
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.application.steps import ParkReason
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.numbering import authorized
from ledger_kernel.errors import NotFoundError
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import Mode
lazy from ledger_country_do.channels.ecf_v1.application.documents import (
    Advances,
    CompanyOptions,
    Deliveries,
    Documents,
    EcfDocument,
    Submissions,
)
lazy from ledger_country_do.channels.ecf_v1.application.received import (
    ApprovalJobs,
    ReceivedDocument,
    ReceivedDocuments,
)
lazy from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.numbering import Allocation, Authorization, RangeState
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_kernel.tenancy import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class NumberingRange:
    """A range as the company registered it: DGII's authorization, where it numbers, when, its version."""

    environment: Environment
    range: RangeState
    created_at: datetime
    version: int

    @property
    def id(self) -> UUID:
        """The range's id."""
        return self.range.id


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RangeRequest:
    """A range a company asks to register: DGII's authorization, for one of the channel's environments."""

    environment: Environment
    authorization: Authorization


class Ranges(Protocol, metaclass=ABCMeta):
    """The ranges of the unit's company in the unit's mode, by series (an environment and an e-CF type)."""

    @abstractmethod
    async def series(self, environment: Environment, ecf_type: EcfType) -> tuple[RangeState, ...]:
        """The series' ranges, its row locked until the unit ends (made, the first time, then locked)."""

    @abstractmethod
    async def add(self, registered: NumberingRange) -> None:
        """Keep a new range of a series ``series`` locked in this unit."""

    @abstractmethod
    async def get(self, range_id: UUID) -> NumberingRange | None:
        """The range, or ``None``."""

    @abstractmethod
    async def page(
        self, request: PageRequest, *, environment: Environment | None, ecf_type: EcfType | None
    ) -> Page[NumberingRange]:
        """The ranges, of ``environment`` and ``ecf_type`` when given, in id order."""

    @abstractmethod
    async def of(self, environment: Environment) -> tuple[RangeState, ...]:
        """Every range of ``environment``, of every type, as it stands."""

    @abstractmethod
    async def available(self, environment: Environment, ecf_type: EcfType, *, now: datetime) -> bool:
        """Whether a new document of the series would get a number at ``now``: a read, nothing locked."""

    @abstractmethod
    async def allocate(self, environment: Environment, ecf_type: EcfType, *, now: datetime) -> Allocation:
        """The number a new document of the series gets at ``now``, taken (``numbering.choose``).

        Under the series' row lock: a released number taken is gone, a range's ``next`` moved on.
        ``NoNumberAvailableError`` when there is none.
        """

    @abstractmethod
    async def release(
        self,
        environment: Environment,
        encf: Encf,
        *,
        document: UUID,
        released_at: datetime,
        available_at: datetime,
    ) -> None:
        """``encf``, rejected by DGII and not used (``document``'s): allocatable from ``available_at``."""


class Announcements(Protocol, metaclass=ABCMeta):
    """What the unit tells others once it commits (its outbox)."""

    @abstractmethod
    def requirements_changed(self) -> None:
        """What the channel requires of the unit's company changed: fiscal reviews its enrollments."""

    @abstractmethod
    def document_updated(self, document: EcfDocument) -> None:
        """``document`` is new, or changed: its state at its version (``fiscal_channel.document.updated``)."""

    @abstractmethod
    def received(self, document: ReceivedDocument) -> None:
        """``document`` was received and recorded (``fiscal_channel.received_document.recorded``)."""


type AuditAction = Literal[
    "numbering_range.register",
    "delivery.retry",
    "delivery_options.set",
    "do_ecf.buyer_seed.signed",
    "received_document.approve",
    "received_document.approval_sent",
]
"""What a use case records it did; the part before the dot names the kind of subject."""


class Audit(Protocol, metaclass=ABCMeta):
    """Who changed what, recorded with the change (``audit_events``, §6.4); the actor is the unit's call."""

    @abstractmethod
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        before: object,
        after: object,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        """Record that ``action`` changed ``subject`` from ``before`` to ``after`` (``None``: not there).

        ``detail``: what else the row says, never a tax id unmasked, a token or a document.
        """


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class NumberingUnit:
    """One unit of work: the channel's numbering and documents in one transaction, and what it announces."""

    tenant: Tenant
    ranges: Ranges
    documents: Documents
    submissions: Submissions
    received: ReceivedDocuments
    """The e-CF other emitters sent the company (the receiver's, Task 22)."""
    advances: Advances
    deliveries: Deliveries
    approvals: ApprovalJobs
    """The ``do_ecf.approve`` jobs: the company's commercial decisions sent out (Task 23)."""
    options: CompanyOptions
    announcements: Announcements
    audit: Audit
    now: datetime
    """The unit's time: everything it records happened then."""


class NumberingUnits(Protocol, metaclass=ABCMeta):
    """Where the use cases get the units they work in."""

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[NumberingUnit]:
        """A unit scoped to ``tenant`` (a company in a mode); it commits when its block ends."""


class Enrollments(Protocol, metaclass=ABCMeta):
    """Whether a company is enrolled on the channel (fiscal's, which the channel cannot read itself)."""

    @abstractmethod
    async def enrolled(self, tenant: Tenant, environment: Environment) -> bool:
        """Whether ``tenant``'s company has an enrollment not disabled in ``environment``, in its mode."""


class EnvironmentNotAllowedError(EcfInvalidError):
    """An environment of the other mode: a live range numbers live documents only, a test one test ones."""

    code: ClassVar[str] = "do_ecf_environment_not_allowed"

    def __init__(self) -> None:
        super().__init__("the environment serves the other mode: ecf is live, testecf and certecf test")


class EnvironmentNotEnrolledError(EcfInvalidError):
    """An environment the company is not enrolled in: enroll first (``/v1/fiscal/enrollments``)."""

    code: ClassVar[str] = "do_ecf_environment_not_enrolled"

    def __init__(self) -> None:
        super().__init__("the company is not enrolled on do-ecf-v1 in this environment and mode")


class RangeNotFoundError(EcfError, NotFoundError):
    """No range of the company in the mode has the id asked for."""

    code: ClassVar[str] = "do_ecf_range_not_found"

    def __init__(self) -> None:
        super().__init__("no such numbering range")


@final
class Ranging:
    """The ranges' use cases (see the module)."""

    __slots__ = ("_enrollments", "_units")

    def __init__(self, units: NumberingUnits, enrollments: Enrollments) -> None:
        self._units = units
        self._enrollments = enrollments

    async def prepare(self, tenant: Tenant, request: RangeRequest) -> RangeRequest:
        """``request``, once its environment is of ``tenant``'s mode and the company is enrolled there.

        Outside any unit: it asks fiscal. ``EnvironmentNotAllowedError``, ``EnvironmentNotEnrolledError``.
        """
        if request.environment.live is not (tenant.mode is Mode.LIVE):
            raise EnvironmentNotAllowedError
        if not await self._enrollments.enrolled(tenant, request.environment):
            raise EnvironmentNotEnrolledError
        return request

    async def register(self, unit: NumberingUnit, request: RangeRequest) -> NumberingRange:
        """Register ``request`` (``prepare``'s) in ``unit``, under its series' lock (see the module)."""
        series = await unit.ranges.series(request.environment, request.authorization.ecf_type)
        state = authorized(request.authorization, range_id=new_id(), series=series)
        registered = NumberingRange(
            environment=request.environment, range=state, created_at=unit.now, version=1
        )
        await unit.ranges.add(registered)
        unit.audit.record("numbering_range.register", registered.id, before=None, after=registered)
        unit.announcements.requirements_changed()
        unit.advances.wake(ParkReason.RANGE_MISSING)
        return registered

    async def page(
        self,
        tenant: Tenant,
        request: PageRequest,
        *,
        environment: Environment | None = None,
        ecf_type: EcfType | None = None,
    ) -> Page[NumberingRange]:
        """The company's ranges in the tenant's mode, oldest first."""
        async with self._units.unit(tenant) as unit:
            return await unit.ranges.page(request, environment=environment, ecf_type=ecf_type)

    async def get(self, tenant: Tenant, range_id: UUID) -> NumberingRange:
        """A range of the company in the tenant's mode (``RangeNotFoundError`` otherwise)."""
        async with self._units.unit(tenant) as unit:
            found = await unit.ranges.get(range_id)
        if found is None:
            raise RangeNotFoundError
        return found
