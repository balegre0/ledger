"""Enrolling a company on a fiscal channel, reviewing what the channel requires, disabling (§7.6).

A channel is asked what an enrollment requires outside any unit (rule 13: it opens its own), with a
``SystemCall`` scoped to the enrollment's tenant; what it answers is applied in a unit that locks the
enrollment. Enrolling is prepared first (the channel and environment checked, the requirements read) and
then applied in the unit the request's ``Idempotency-Key`` is claimed in, so that a retry replays the first
answer and a failure keeps nothing. Every change is audited and announced (``fiscal.enrollment.changed``).
"""

from dataclasses import dataclass
from typing import Final, final

from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId, EnrollmentStatus
from ledger_fiscal.domain.errors import (
    ChannelNotInstalledError,
    EnrollmentExistsError,
    EnrollmentNotFoundError,
    EnvironmentNotAllowedError,
    EnvironmentUnknownError,
)
from ledger_fiscal.events import EnrollmentChanged
from ledger_fiscal_kit.channels import EnrollmentId as ChannelEnrollmentId
from ledger_fiscal_kit.channels import EnrollmentState
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import new_id
from ledger_kernel.tenancy import Tenant
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.contexts import ChannelUnknownError
lazy from ledger_fiscal.application.ports import AuditAction, FiscalUnit, FiscalUnits
lazy from ledger_fiscal_kit.channels import Channel, ChannelRegistry, Requirements
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_platform.runtime.calls import Call

REQUIREMENTS_REASON: Final = "fiscal:requirements"
"""The reason of the system call a channel is asked an enrollment's requirements in."""


def live_evidence(enrollment: Enrollment) -> bool:
    """Whether the company's authority authorization is recorded with evidence (§7.6 C4).

    Recording it is Task 31's operator command: until then there is none, so no live enrollment activates.
    """
    del enrollment
    return False


def tenant_of(call: Call) -> Tenant:
    """The company and mode a request acts in; a call scoped otherwise is a defect of its route."""
    scope = call.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="call.scope", requirement="a company in a mode")
    return scope


def _state(enrollment: Enrollment) -> EnrollmentState:
    return EnrollmentState(
        enrollment=ChannelEnrollmentId(enrollment.id),
        organization=enrollment.organization,
        company=enrollment.company,
        mode=enrollment.mode,
        environment=enrollment.environment,
        active=enrollment.active,
    )


def _changed(unit: FiscalUnit, action: AuditAction, before: Enrollment | None, after: Enrollment) -> None:
    unit.audit.record(action, after.id, before=before, after=after)
    unit.events.publish(
        EnrollmentChanged(
            scope=after.tenant,
            subject=after.id,
            aggregate_version=after.version,
            enrollment=after.id,
            channel=after.channel,
            environment=after.environment,
            status=after.status.value,
        )
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Prepared:
    """An enrollment checked and its requirements read, to be kept in the unit that claims the request."""

    asked: Asked
    requirements: Requirements


@final
class Enrolling:
    """fiscal's use cases on enrollments."""

    __slots__ = ("_channels", "_units")

    def __init__(self, units: FiscalUnits, channels: ChannelRegistry) -> None:
        self._units = units
        self._channels = channels

    def _channel(self, key: str) -> Channel | None:
        try:
            return self._channels.get(key)
        except ChannelUnknownError:
            return None

    async def _requirements(self, call: Call, channel: Channel, state: EnrollmentState) -> Requirements:
        tenant = Tenant(state.organization, state.company, state.mode)
        system = SystemCall(reason=REQUIREMENTS_REASON, scope=tenant, traceparent=call.traceparent)
        return await channel.requirements(system, state)

    async def prepare(self, call: Call, *, channel: str, environment: str) -> Prepared:
        """``channel`` and ``environment`` checked for the call's mode, and what the channel requires now.

        ``ChannelNotInstalledError``, ``EnvironmentUnknownError``, ``EnvironmentNotAllowedError`` (an
        environment of the other mode, in either mode).
        """
        tenant = tenant_of(call)
        found = self._channel(channel)
        if found is None:
            raise ChannelNotInstalledError
        definition = found.definition
        if environment not in definition.environments:
            raise EnvironmentUnknownError
        mode = tenant.mode
        if mode is None or not definition.allows(environment, mode):
            raise EnvironmentNotAllowedError
        enrollment = EnrollmentId(new_id())
        state = EnrollmentState(
            enrollment=ChannelEnrollmentId(enrollment),
            organization=tenant.organization,
            company=tenant.company,
            mode=mode,
            environment=environment,
            active=False,
        )
        requirements = await self._requirements(call, found, state)
        asked = Asked(enrollment=enrollment, tenant=tenant, channel=channel, environment=environment)
        return Prepared(asked=asked, requirements=requirements)

    @staticmethod
    async def enroll(unit: FiscalUnit, prepared: Prepared) -> Enrollment:
        """Keep ``prepared``'s enrollment in ``unit``; ``EnrollmentExistsError`` names the one holding it."""
        new = Enrollment.enroll(prepared.asked, requirements=prepared.requirements, now=unit.now)
        holder = await unit.enrollments.add(new)
        if holder is not None:
            raise EnrollmentExistsError(holder)
        _changed(unit, "enrollment.enroll", None, new)
        return new

    async def review(self, call: Call, enrollment: EnrollmentId) -> Enrollment:
        """The enrollment with what its channel requires now, kept if that changed it (Decision 31).

        A disabled enrollment, or one whose channel is no longer installed, is answered as kept.
        """
        tenant = tenant_of(call)
        async with self._units.unit(tenant) as unit:
            found = await unit.enrollments.get(enrollment)
        if found is None:
            raise EnrollmentNotFoundError
        channel = self._channel(found.channel)
        if found.status is EnrollmentStatus.DISABLED or channel is None:
            return found
        requirements = await self._requirements(call, channel, _state(found))
        async with self._units.unit(tenant) as unit:
            current = await unit.enrollments.locked(enrollment)
            if current is None:  # read a moment ago in this tenant: never deleted, so a defect
                raise EnrollmentNotFoundError
            reviewed = current.reviewed(requirements, live_evidence=live_evidence(current), now=unit.now)
            if reviewed is not current:
                await unit.enrollments.update(reviewed, version=current.version)
                _changed(unit, "enrollment.review", current, reviewed)
            return reviewed

    @staticmethod
    async def disable(unit: FiscalUnit, enrollment: EnrollmentId, reason: str) -> Enrollment:
        """The enrollment disabled for ``reason``, in ``unit``; itself when it is disabled already."""
        current = await unit.enrollments.locked(enrollment)
        if current is None:
            raise EnrollmentNotFoundError
        disabled = current.disabled(reason, now=unit.now)
        if disabled is not current:
            await unit.enrollments.update(disabled, version=current.version)
            _changed(unit, "enrollment.disable", current, disabled)
        return disabled

    async def page(self, call: Call, request: PageRequest) -> Page[Enrollment]:
        """The enrollments of the call's company and mode, as kept, in id order."""
        async with self._units.unit(tenant_of(call)) as unit:
            return await unit.enrollments.page(request)
