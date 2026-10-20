"""The enrollment use cases over in-memory units: what the HTTP tests cannot reach.

The stand-in units keep enrollments in a dict per tenant and record what they announce and audit; a test may
make an enrollment vanish between the read of a review and its locked read (a row is never deleted in
PostgreSQL, so only a defect could do it).
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final, final, override
lazy from collections.abc import AsyncGenerator
lazy from uuid import UUID

import pytest

from ledger_fiscal.api import FISCAL_API, FiscalApi
from ledger_fiscal.application.enrolling import Enrolling, tenant_of
from ledger_fiscal.application.facade import FiscalFacade, organization_of
from ledger_fiscal.application.ports import Audit, Enrollments, Events, FiscalUnit, FiscalUnits
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId, EnrollmentStatus
from ledger_fiscal.domain.errors import EnrollmentNotFoundError
from ledger_fiscal_kit.channels import ChannelRegistry, Requirements
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.pagination import page_of
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.contexts import AssembledChannels
lazy from ledger_fiscal.application.ports import AuditAction
lazy from ledger_fiscal.events import FiscalEvent
lazy from ledger_fiscal_kit.channels import Channel
lazy from ledger_kernel.pagination import Page, PageRequest

NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.TEST)
PRINCIPAL: Final = Principal(
    subject=SubjectId("2001"),
    organization=TENANT.organization,
    provider_organization="1001",
    kind=PrincipalKind.HUMAN,
)
CALL: Final = Call(principal=PRINCIPAL, scope=TENANT, request_id="r-1")


@final
class MemoryEnrollments(Enrollments):
    """One tenant's enrollments in a dict; ``vanishing`` ones are gone when locked."""

    __slots__ = ("kept", "vanishing")

    def __init__(self) -> None:
        self.kept: dict[UUID, Enrollment] = {}
        self.vanishing: set[UUID] = set()

    @override
    async def add(self, enrollment: Enrollment) -> EnrollmentId | None:
        self.kept[enrollment.id] = enrollment
        return None

    @override
    async def get(self, enrollment: EnrollmentId) -> Enrollment | None:
        return self.kept.get(enrollment)

    @override
    async def locked(self, enrollment: EnrollmentId) -> Enrollment | None:
        return None if enrollment in self.vanishing else self.kept.get(enrollment)

    @override
    async def update(self, enrollment: Enrollment, *, version: int) -> None:
        del version
        self.kept[enrollment.id] = enrollment

    @override
    async def on(self, channel: str, environment: str) -> Enrollment | None:
        del channel, environment
        return None

    @override
    async def every(self) -> tuple[Enrollment, ...]:
        return tuple(sorted(self.kept.values(), key=lambda found: found.id))

    @override
    async def page(self, request: PageRequest) -> Page[Enrollment]:
        return page_of(await self.every(), request, key=lambda found: found.id)


@final
class Recorded(Events, Audit):
    """What the units announced and audited."""

    __slots__ = ("actions", "events")

    def __init__(self) -> None:
        self.events: list[FiscalEvent] = []
        self.actions: list[AuditAction] = []

    @override
    def publish(self, event: FiscalEvent) -> None:
        self.events.append(event)

    @override
    def record(self, action: AuditAction, subject: UUID, *, before: object, after: object) -> None:
        del subject, before, after
        self.actions.append(action)


@final
class MemoryUnits(FiscalUnits):
    """Units over one tenant's ``MemoryEnrollments``."""

    __slots__ = ("enrollments", "recorded", "tenants")

    def __init__(self) -> None:
        self.enrollments = MemoryEnrollments()
        self.recorded = Recorded()
        self.tenants: list[Tenant] = []

    @override
    @asynccontextmanager
    async def unit(self, tenant: Tenant) -> AsyncGenerator[FiscalUnit]:
        self.tenants.append(tenant)
        yield FiscalUnit(
            tenant=tenant, enrollments=self.enrollments, events=self.recorded, audit=self.recorded, now=NOW
        )


def registry(*channels: tuple[str, Channel]) -> ChannelRegistry:
    assembled = AssembledChannels()
    for key, channel in channels:
        assembled.add(key, channel)
    assembled.seal()
    return ChannelRegistry(assembled)


def kept(units: MemoryUnits, channel: str = "do-fake-v1") -> Enrollment:
    asked = Asked(enrollment=EnrollmentId(new_id()), tenant=TENANT, channel=channel, environment="testecf")
    enrollment = Enrollment.enroll(asked, requirements=Requirements(currently_due=("x",)), now=NOW)
    units.enrollments.kept[enrollment.id] = enrollment
    return enrollment


async def test_a_review_of_an_enrollment_whose_channel_is_gone_answers_it_as_kept() -> None:
    units = MemoryUnits()
    enrollment = kept(units, channel="do-gone-v1")
    assert await Enrolling(units, registry()).review(CALL, enrollment.id) is enrollment
    assert units.recorded.events == []


async def test_an_enrollment_that_vanished_while_its_channel_was_asked_is_not_found(
    fake_channel: Channel,
) -> None:
    units = MemoryUnits()
    enrollment = kept(units)
    units.enrollments.vanishing.add(enrollment.id)
    with pytest.raises(EnrollmentNotFoundError):
        await Enrolling(units, registry(("do-fake-v1", fake_channel))).review(CALL, enrollment.id)


@pytest.mark.parametrize(
    "scope",
    [OrganizationScope(TENANT.organization, Mode.TEST), Tenant(TENANT.organization, TENANT.company, None)],
)
async def test_a_request_acts_in_a_company_in_a_mode_or_it_is_a_defect(
    scope: OrganizationScope | Tenant,
) -> None:
    call = Call(principal=PRINCIPAL, scope=scope, request_id="r-1")
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        tenant_of(call)


async def test_the_api_answers_a_company_s_enrollments_in_a_mode_of_the_call_s_organization() -> None:
    units = MemoryUnits()
    enrollment = kept(units)
    facade = FiscalFacade(units)
    assert FISCAL_API.take(facade) is facade
    assert isinstance(facade, FiscalApi)
    system = SystemCall(reason="test", scope=OrganizationScope(TENANT.organization, None))
    (view,) = await facade.enrollments(system, TENANT.company, Mode.TEST)
    assert (view.id, view.status, view.currently_due, view.version) == (enrollment.id, "pending", ("x",), 1)
    assert units.tenants == [TENANT]
    assert view.status == EnrollmentStatus.PENDING.value
    assert organization_of(CALL) == TENANT.organization
    with pytest.raises(InvalidValueError, match=r"call\.scope"):
        organization_of(SystemCall(reason="test", scope=None))
