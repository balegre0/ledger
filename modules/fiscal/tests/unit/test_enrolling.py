"""The enrollment use cases and the directory over in-memory units and a stand-in identity: what the HTTP
tests cannot reach.

The stand-in units keep enrollments in a dict and record what they announce and audit; a test may make an
enrollment vanish between the read of a review and its locked read (a row is never deleted in PostgreSQL, so
only a defect could do it), or move it on meanwhile (another review won). The stand-in identity answers one
company, of the jurisdiction and activity a test gives it.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Final, Protocol, final, override
lazy from collections.abc import AsyncGenerator
lazy from uuid import UUID

import pytest

from ledger_fiscal.api import FISCAL_API, FiscalApi
from ledger_fiscal.application.directory import FiscalDirectory
from ledger_fiscal.application.enrolling import Enrolling, governs, tenant_of
from ledger_fiscal.application.facade import FiscalFacade, organization_of
from ledger_fiscal.application.ports import (
    Audit,
    Enrollments,
    Events,
    FiscalUnit,
    FiscalUnits,
    Jobs,
    Routings,
)
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId, EnrollmentStatus
from ledger_fiscal.domain.errors import (
    ChannelNotForJurisdictionError,
    ChannelNotInForceError,
    EnrollmentNotFoundError,
)
from ledger_fiscal_kit.channels import Channel, ChannelRegistry, Requirements
from ledger_fiscal_kit.ports import SignerUnavailableError
from ledger_identity.api import CompanyInfo, IdentityApi
from ledger_kernel.access import Principal, PrincipalKind, SubjectId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.jurisdiction import parse_jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.pagination import page_of
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, OrganizationScope, Tenant
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.contexts import AssembledChannels
from ledger_platform.runtime.facade import facade
from ledger_platform.runtime.registries import Regimes
lazy from ledger_fiscal.application.ports import AuditAction
lazy from ledger_fiscal.domain.routing import Routing
lazy from ledger_fiscal.events import FiscalEvent
lazy from ledger_fiscal_kit.channels import ChannelDef
lazy from ledger_identity.api import EstablishmentInfo
lazy from ledger_kernel.access import Permission
lazy from ledger_kernel.errors import LedgerError
lazy from ledger_kernel.jurisdiction import Jurisdiction
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
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
REGIMES: Final = Regimes.load()
"""The installed regimes: the Dominican one, which the fake channel serves."""


def company(jurisdiction: Jurisdiction, *, active: bool = True) -> CompanyInfo:
    return CompanyInfo(
        id=TENANT.company,
        organization=TENANT.organization,
        jurisdiction=jurisdiction,
        functional_currency=Currency.of("DOP"),
        tax_ids=(RNC,),
        legal_name="Acme SRL",
        address=None,
        active=active,
        fiscal_profile_locked=False,
        version=1,
    )


@final
class StubIdentity(IdentityApi):
    """identity answering one company, whatever it is asked."""

    __slots__ = ("found",)

    def __init__(self, found: CompanyInfo) -> None:
        self.found = found

    @override
    @facade
    async def company(self, call: Call | SystemCall, company: CompanyId) -> CompanyInfo:
        del call, company
        return self.found

    @override
    @facade
    async def company_by_tax_id(self, call: Call | SystemCall, tax_id: TaxId) -> CompanyInfo | None:
        del call, tax_id
        return self.found

    @override
    @facade
    async def permissions(
        self, call: Call | SystemCall, principal: Principal, company: CompanyId
    ) -> frozenset[Permission]:
        del call, principal, company
        return frozenset()

    @override
    @facade
    async def establishment(
        self, call: Call | SystemCall, company: CompanyId, code: str | None
    ) -> EstablishmentInfo | None:
        del call, company, code
        return None

    @override
    @facade
    async def lock_fiscal_profile(self, call: SystemCall, company: CompanyId) -> CompanyInfo:
        del call, company
        return self.found


DOMINICAN: Final = StubIdentity(company(parse_jurisdiction("DO")))


class FakeChannel(Channel, Protocol):
    """The conftest's channel, as these tests set it: its definition, and a failure of its requirements."""

    defined: ChannelDef
    failure: LedgerError | None
    due: Requirements


@final
class MemoryEnrollments(Enrollments):
    """One tenant's enrollments in a dict: ``vanishing`` ones gone when locked, ``moving`` ones moved on."""

    __slots__ = ("kept", "moving", "updates", "vanishing")

    def __init__(self) -> None:
        self.kept: dict[UUID, Enrollment] = {}
        self.vanishing: set[UUID] = set()
        self.moving: set[UUID] = set()
        self.updates = 0

    @override
    async def add(self, enrollment: Enrollment) -> EnrollmentId | None:
        self.kept[enrollment.id] = enrollment
        return None

    @override
    async def get(self, enrollment: EnrollmentId) -> Enrollment | None:
        return self.kept.get(enrollment)

    @override
    async def locked(self, enrollment: EnrollmentId) -> Enrollment | None:
        if enrollment in self.vanishing:
            return None
        found = self.kept[enrollment]
        if enrollment in self.moving:  # another review won meanwhile: a version on
            found = replace(found, currently_due=("y",), version=found.version + 1, updated_at=NOW)
            self.kept[enrollment] = found
        return found

    @override
    async def update(self, enrollment: Enrollment, *, version: int) -> None:
        del version
        self.updates += 1
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

    @override
    async def mark_due(self, channel: str) -> None:
        for identifier, enrollment in tuple(self.kept.items()):
            if enrollment.channel == channel:
                self.kept[identifier] = replace(enrollment, review_due=True)


@final
class NoRoutings(Routings, Jobs):
    """Enrolling routes nothing and runs no job: any use of these is a defect of the code under test."""

    @override
    async def add(self, routing: Routing) -> Routing:
        raise AssertionError

    @override
    async def of(self, invoice: UUID) -> tuple[Routing, ...]:
        raise AssertionError

    @override
    async def get(self, invoice: UUID, channel: str) -> Routing | None:
        raise AssertionError

    @override
    def route_later(self, invoice: UUID, channel: str) -> None:
        raise AssertionError

    @override
    def wake_routes(self) -> None:
        raise AssertionError


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
            tenant=tenant,
            enrollments=self.enrollments,
            routings=NO_ROUTINGS,
            jobs=NO_ROUTINGS,
            events=self.recorded,
            audit=self.recorded,
            now=NOW,
        )


NO_ROUTINGS: Final = NoRoutings()


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


def enrolling(
    units: MemoryUnits, *channels: tuple[str, Channel], identity: IdentityApi = DOMINICAN
) -> Enrolling:
    return Enrolling(units, registry(*channels), identity=identity, regimes=REGIMES, clock=lambda: NOW)


async def test_a_review_of_an_enrollment_whose_channel_is_gone_answers_it_as_kept() -> None:
    units = MemoryUnits()
    enrollment = kept(units, channel="do-gone-v1")
    assert await enrolling(units).review(CALL, enrollment.id) is enrollment
    assert units.recorded.events == []


async def test_an_enrollment_that_vanished_while_its_channel_was_asked_is_not_found(
    fake_channel: Channel,
) -> None:
    units = MemoryUnits()
    enrollment = kept(units)
    units.enrollments.vanishing.add(enrollment.id)
    with pytest.raises(EnrollmentNotFoundError):
        await enrolling(units, ("do-fake-v1", fake_channel)).review(CALL, enrollment.id)


async def test_a_review_another_one_overtook_answers_the_other_s_and_keeps_nothing(
    fake_channel: Channel,
) -> None:
    units = MemoryUnits()
    enrollment = kept(units)
    units.enrollments.moving.add(enrollment.id)
    reviewed = await enrolling(units, ("do-fake-v1", fake_channel)).review(CALL, enrollment.id)
    assert (reviewed.version, reviewed.currently_due) == (2, ("y",))
    assert (units.enrollments.updates, units.recorded.events, units.recorded.actions) == (0, [], [])


async def test_a_review_whose_channel_cannot_answer_now_answers_the_enrollment_as_kept(
    fake_channel: FakeChannel,
) -> None:
    units = MemoryUnits()
    enrollment = kept(units)
    fake_channel.failure = SignerUnavailableError()
    assert await enrolling(units, ("do-fake-v1", fake_channel)).review(CALL, enrollment.id) is enrollment
    assert (units.enrollments.updates, units.recorded.events) == (0, [])


async def test_current_reviews_the_enrollments_a_channel_marked_and_answers_the_others_as_kept(
    fake_channel: FakeChannel,
) -> None:
    """Decision 34: ``fiscal.requirements`` marks; the next read that routes a document reviews the marked
    ones (clearing the mark, a version on only when something else changed) and asks nothing of the rest."""
    units = MemoryUnits()
    marked = kept(units)
    other = kept(units, channel="do-other-v1")
    await units.enrollments.mark_due("do-fake-v1")
    fake_channel.due = Requirements(currently_due=("x",))
    found = await enrolling(units, ("do-fake-v1", fake_channel)).current(TENANT, traceparent=None)
    assert {each.id: (each.review_due, each.version) for each in found} == {
        marked.id: (False, 1),
        other.id: (False, 1),
    }
    assert (units.enrollments.updates, units.recorded.events, units.recorded.actions) == (1, [], [])
    await units.enrollments.mark_due("do-fake-v1")
    fake_channel.due = Requirements(currently_due=())
    (active, _) = sorted(
        await enrolling(units, ("do-fake-v1", fake_channel)).current(TENANT, traceparent=None),
        key=lambda each: each.channel != "do-fake-v1",
    )
    assert (active.status, active.version, active.review_due) == (EnrollmentStatus.ACTIVE, 2, False)
    assert units.recorded.actions == ["enrollment.review"]


async def test_a_channel_of_another_jurisdiction_is_refused_before_it_is_asked(
    fake_channel: FakeChannel,
) -> None:
    foreign = StubIdentity(company(parse_jurisdiction("US")))
    with pytest.raises(ChannelNotForJurisdictionError):
        await enrolling(MemoryUnits(), ("do-fake-v1", fake_channel), identity=foreign).prepare(
            CALL, channel="do-fake-v1", environment="testecf"
        )
    prepared = await enrolling(MemoryUnits(), ("do-fake-v1", fake_channel)).prepare(
        CALL, channel="do-fake-v1", environment="testecf"
    )
    assert prepared.asked.tenant == TENANT


async def test_a_channel_that_is_not_in_force_today_where_its_regime_keeps_its_days_is_refused(
    fake_channel: FakeChannel,
) -> None:
    """``NOW`` is 2026-10-19 08:00 in Santo Domingo: a channel valid until the 18th is no longer in force."""
    for defined in (
        replace(fake_channel.defined, valid_from=date(2026, 10, 20)),
        replace(fake_channel.defined, valid_until=date(2026, 10, 18)),
    ):
        fake_channel.defined = defined
        with pytest.raises(ChannelNotInForceError):
            await enrolling(MemoryUnits(), ("do-fake-v1", fake_channel)).prepare(
                CALL, channel="do-fake-v1", environment="testecf"
            )
    fake_channel.defined = replace(defined, valid_from=date(2026, 10, 19), valid_until=date(2026, 10, 19))
    await enrolling(MemoryUnits(), ("do-fake-v1", fake_channel)).prepare(
        CALL, channel="do-fake-v1", environment="testecf"
    )  # its first and last day, both included


@pytest.mark.parametrize(
    "case",
    [
        (parse_jurisdiction("DO"), parse_jurisdiction("DO"), True),
        (parse_jurisdiction("DO"), parse_jurisdiction("DO", "DO-01"), True),
        (parse_jurisdiction("US", "US-NY"), parse_jurisdiction("US", "US-NY"), True),
        (parse_jurisdiction("US", "US-NY"), parse_jurisdiction("US"), False),
        (parse_jurisdiction("US", "US-NY"), parse_jurisdiction("US", "US-CA"), False),
        (parse_jurisdiction("DO"), parse_jurisdiction("US"), False),
    ],
    ids=[
        "country",
        "country-of-a-subdivision",
        "subdivision",
        "subdivision-not-country",
        "other-subdivision",
        "other-country",
    ],
)
def test_a_regime_governs_its_country_or_its_subdivision_only(
    case: tuple[Jurisdiction, Jurisdiction, bool],
) -> None:
    regime, company, governed = case
    assert governs(regime, company) is governed


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
    assert tenant_of(CALL) == (TENANT, Mode.TEST)


async def test_an_inactive_company_is_named_by_no_directory(fake_channel: Channel) -> None:
    units = MemoryUnits()
    directory = FiscalDirectory(
        identity=StubIdentity(company(parse_jurisdiction("DO"), active=False)),
        units=units,
        channels=registry(("do-fake-v1", fake_channel)),
    )
    system = SystemCall(reason="test", scope=None)
    assert (
        await directory.receiving_company(system, tax_id=RNC, channel="do-fake-v1", environment="testecf")
        is None
    )
    assert units.tenants == []  # not even looked for


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
