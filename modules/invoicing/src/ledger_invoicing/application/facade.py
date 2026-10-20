"""invoicing's API for the other modules (``api.InvoicingApi``), over its use cases.

Each method opens the unit its use case needs, in the tenant the call names, and gives back data
(``InvoiceInfo``, ``Applied``), never a domain object. ``@facade`` refuses a call made while the caller holds
a unit.
"""

from typing import final, override

from ledger_invoicing.api import InvoicingApi
from ledger_invoicing.application.infos import info_of
from ledger_invoicing.domain.fiscal import AuthorityMessage, FiscalStatus
from ledger_platform.runtime.facade import facade
lazy from ledger_invoicing.api import Applied, FiscalStatusInfo, InvoiceInfo
lazy from ledger_invoicing.application.mirror import FiscalMirroring
lazy from ledger_invoicing.application.queries import InvoicingQueries
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_platform.runtime.calls import AnyCall, SystemCall


def status_of(info: FiscalStatusInfo, version: int) -> FiscalStatus:
    """The mirror's value of what a channel reported at ``version`` (``InvalidValueError`` out of bounds)."""
    return FiscalStatus(
        channel=info.channel,
        environment=info.environment,
        status=info.status,
        version=version,
        authority_number=info.authority_number,
        messages=tuple(
            AuthorityMessage(code=message.code, message=message.message, lang=message.lang)
            for message in info.messages
        ),
        regime=info.regime,
        details=info.details,
        updated_at=info.updated_at,
    )


@final
class InvoicingFacade(InvoicingApi):
    """The API, as invoicing implements it."""

    __slots__ = ("_mirroring", "_queries")

    def __init__(self, queries: InvoicingQueries, mirroring: FiscalMirroring) -> None:
        self._queries = queries
        self._mirroring = mirroring

    @override
    @facade
    async def document(self, call: AnyCall, invoice: InvoiceId) -> InvoiceInfo:
        return info_of(await self._queries.document(call, invoice))

    @override
    @facade
    async def apply_fiscal_status(
        self, call: SystemCall, document: InvoiceId, status: FiscalStatusInfo, version: int
    ) -> Applied:
        return await self._mirroring.apply(call, document, status_of(status, version))
