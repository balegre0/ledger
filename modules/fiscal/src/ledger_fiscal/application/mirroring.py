r"""fiscal's consumers (§3.5, §8.5): the recovery path, the status mirror, and the channels' requirements.

- ``fiscal.commercial_documents`` (outside, ``invoicing.invoice.issued``): routes every issued document to
  its channels without signing (``Router.recover``), whether or not the request did; each step is
  idempotent by (document, channel), and a redelivery finds the work done.
- ``fiscal.channel_documents`` (outside, ``fiscal_channel.document.updated``): keeps the channel's status in
  invoicing's mirror at its version + 1 (``issuing.mirrored_version``). Version-monotonic, so unordered: an
  older or repeated update is ``STALE`` and changes nothing; one for a voided document is not kept.
- ``fiscal.requirements`` (inside, ``fiscal_channel.requirements.changed``, Decision 34): marks the company's
  enrollments on that channel not disabled ``review_due`` and readies its routings parked for a number, in
  its own unit only: no facade or channel is called inside a unit (rule 13); the next read reviews them.

An outside consumer's calls are ``SystemCall``\ s scoped to the event's tenant, carrying the event's id
(``call_id``, rule 4). An event about anything but a company in a mode, or without the document it is
about, is a defect of its producer (``InvalidValueError``: dead at once).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from uuid import UUID

from ledger_fiscal.application.issuing import mirrored, mirrored_version
from ledger_fiscal_kit.events import DocumentUpdated
from ledger_invoicing.api import InvoiceId
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_fiscal.application.issuing import Router
lazy from ledger_fiscal.application.ports import FiscalUnit
lazy from ledger_fiscal_kit.channels import ChannelRegistry
lazy from ledger_fiscal_kit.events import RequirementsChanged
lazy from ledger_invoicing.api import InvoicingApi
lazy from ledger_platform.events.consumers import RawEvent

COMMERCIAL_DOCUMENTS: Final = "fiscal.commercial_documents"
CHANNEL_DOCUMENTS: Final = "fiscal.channel_documents"
REQUIREMENTS: Final = "fiscal.requirements"


def _call(consumer: str, event: RawEvent) -> SystemCall:
    """The call a consumer makes about ``event``: its tenant, its id as the call's (rule 4)."""
    scope = event.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="raw_event.scope", requirement="a company's tenant in a mode")
    return SystemCall(reason=consumer, scope=scope, call_id=str(event.id), traceparent=event.traceparent)


def _subject(event: RawEvent) -> UUID:
    subject = event.subject
    if subject is None:
        raise InvalidValueError(field="raw_event.subject", requirement="the document the event is about")
    return subject


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Consumers:
    """fiscal's consumers' handlers (see the module)."""

    router: Router
    invoicing: InvoicingApi
    channels: ChannelRegistry

    async def on_issued(self, event: RawEvent) -> None:
        """``fiscal.commercial_documents``: the recovery path for the issued document."""
        await self.router.recover(_call(COMMERCIAL_DOCUMENTS, event), _subject(event))

    async def on_channel_document(self, event: RawEvent) -> None:
        """``fiscal.channel_documents``: the channel's status kept in invoicing's mirror, if newer."""
        call = _call(CHANNEL_DOCUMENTS, event)
        update = DocumentUpdated.read(event).update
        regime = self.channels.get(update.channel).definition.regime
        _ = await self.invoicing.apply_fiscal_status(
            call, InvoiceId(update.document), mirrored(update, regime), mirrored_version(update)
        )

    @staticmethod
    async def on_requirements_changed(unit: FiscalUnit, changed: RequirementsChanged) -> None:
        """``fiscal.requirements``, in its unit: the enrollments marked, the parked routings readied."""
        await unit.enrollments.mark_due(changed.channel)
        unit.jobs.wake_routes()
