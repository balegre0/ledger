"""fiscal's consumers and job as the worker runs them: the platform's handlers over the use cases.

``fiscal.requirements`` runs inside the unit its inbox row is deleted in, as fiscal's unit (``bind``). The
``fiscal.route`` job routes its document to its channel again: ``Done`` once the channel numbered it,
``Park("range_missing")`` while it has no number to give (``UnitJobs.wake_routes`` readies it). A job
without its payload, or of anything but a company in a mode, is a defect of whoever enqueued it
(``InvalidValueError``).
"""

from dataclasses import dataclass
from typing import Final
lazy from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from ledger_fiscal.adapters.postgres.unit import RANGE_MISSING, bind
from ledger_fiscal.application.mirroring import Consumers
from ledger_fiscal_kit.events import RequirementsChanged
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.jobs.outcomes import Done, Park
lazy from ledger_fiscal.application.issuing import Router
lazy from ledger_platform.data import UnitOfWork
lazy from ledger_platform.events.consumers import Handler, RawEvent
lazy from ledger_platform.jobs.outcomes import Job, JobHandler, JobOutcome


@dataclass(frozen=True, slots=True, kw_only=True)
class _Route:
    invoice: UUID
    channel: str


_PAYLOAD: Final = TypeAdapter(_Route)


def requirements_changed() -> Handler:
    """``fiscal.requirements``'s handler: ``Consumers.on_requirements_changed`` in the inbox row's unit."""

    async def handle(event: RawEvent, uow: UnitOfWork) -> None:
        await Consumers.on_requirements_changed(bind(uow), RequirementsChanged.read(event))

    return handle


def route_job(router: Router) -> JobHandler:
    """``fiscal.route``'s handler (see the module)."""

    async def handle(job: Job) -> JobOutcome:
        scope = job.scope
        if not isinstance(scope, Tenant) or scope.mode is None:
            raise InvalidValueError(field="job.scope", requirement="a company's tenant in a mode")
        try:
            route = _PAYLOAD.validate_python(dict(job.payload))
        except ValidationError as error:
            raise InvalidValueError(field="job.payload", requirement="a document and a channel") from error
        numbered = await router.retry(scope, route.invoice, route.channel)
        return Done() if numbered else Park(RANGE_MISSING)

    return handle
