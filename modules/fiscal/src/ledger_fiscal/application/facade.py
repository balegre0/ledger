"""fiscal's API for the other modules (``api.FiscalApi``), over its units.

Each method opens the unit it needs, in the organization the call names, and gives back data
(``EnrollmentView``), never a domain object. ``@facade`` refuses a call made while the caller holds a unit.
"""

from typing import final, override

from ledger_fiscal.api import EnrollmentView, FiscalApi
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.runtime.calls import Call, SystemCall
from ledger_platform.runtime.facade import facade
lazy from ledger_fiscal.application.ports import FiscalUnits
lazy from ledger_fiscal.domain.enrollments import Enrollment
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import AnyCall


def view(enrollment: Enrollment) -> EnrollmentView:
    """What other modules are told of ``enrollment``."""
    return EnrollmentView(
        id=enrollment.id,
        company=enrollment.company,
        mode=enrollment.mode,
        channel=enrollment.channel,
        environment=enrollment.environment,
        status=enrollment.status.value,
        currently_due=enrollment.currently_due,
        requirements_reason=enrollment.requirements_reason,
        disabled_reason=None if enrollment.disablement is None else enrollment.disablement.reason,
        disabled_at=None if enrollment.disablement is None else enrollment.disablement.at,
        version=enrollment.version,
        updated_at=enrollment.updated_at,
    )


def organization_of(call: AnyCall) -> OrganizationId:
    """The organization ``call`` acts for; a call that names none is a defect of its caller."""
    match call:
        case Call(scope=scope) | SystemCall(scope=scope) if scope is not None:
            return scope.organization
        case _:
            raise InvalidValueError(field="call.scope", requirement="an organization or company scope")


@final
class FiscalFacade(FiscalApi):
    """The API, as fiscal implements it."""

    __slots__ = ("_units",)

    def __init__(self, units: FiscalUnits) -> None:
        self._units = units

    @override
    @facade
    async def enrollments(self, call: AnyCall, company: CompanyId, mode: Mode) -> tuple[EnrollmentView, ...]:
        async with self._units.unit(Tenant(organization_of(call), company, mode)) as unit:
            return tuple(view(enrollment) for enrollment in await unit.enrollments.every())
