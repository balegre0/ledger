"""fiscal's ``Renderer`` (Decision 22): a document's printed representation, as its channel renders it.

A document never routed to the channel has none here (``None``: invoicing answers 404
``representation_unavailable``), nor has one whose channel is no longer installed. A channel that cannot
render it now raises its own ``UnavailableError`` (invoicing answers 503).
"""

from typing import Final, final, override

from ledger_fiscal.application.issuing import tenant_of
from ledger_fiscal.application.routing import installed
from ledger_fiscal_kit.drafts import DocumentRef
from ledger_invoicing.api import RenderedDocument, Renderer
from ledger_platform.runtime.calls import SystemCall
from ledger_platform.runtime.facade import facade
lazy from ledger_fiscal.application.ports import FiscalUnits
lazy from ledger_fiscal_kit.channels import ChannelRegistry
lazy from ledger_invoicing.api import InvoiceInfo
lazy from ledger_platform.runtime.calls import Call

RENDER_REASON: Final = "fiscal.render"
"""The reason of the call the channel is asked a representation in."""


@final
class FiscalRenderer(Renderer):
    """invoicing's ``Renderer``, as fiscal implements it (see the module)."""

    __slots__ = ("_channels", "_units")

    def __init__(self, units: FiscalUnits, channels: ChannelRegistry) -> None:
        self._units = units
        self._channels = channels

    @override
    @facade
    async def render(self, call: Call, document: InvoiceInfo, channel: str) -> RenderedDocument | None:
        tenant = tenant_of(document)
        async with self._units.unit(tenant) as unit:
            routing = await unit.routings.get(document.id, channel)
        found = None if routing is None else installed(self._channels, channel)
        if found is None:
            return None
        system = SystemCall(reason=RENDER_REASON, scope=tenant, traceparent=call.traceparent)
        printed = await found.representation(system, DocumentRef(document.id))
        return RenderedDocument(
            media_type=printed.media_type, content=printed.content, filename=printed.filename
        )
