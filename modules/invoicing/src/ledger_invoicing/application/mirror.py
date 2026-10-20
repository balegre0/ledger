"""The fiscal mirror's use case (§7.5, §8.5): keep what a fiscal channel reports of a document, if newer.

``fiscal`` calls it (``InvoicingApi.apply_fiscal_status``) from its synchronous path and from its consumer of
the channels' ``fiscal_channel.document.updated``, which may deliver an update twice or out of order: a
version not newer than the one kept for the channel is ``STALE`` and changes nothing; a newer one is kept and
announced (``invoicing.invoice.fiscal_updated``) in the same unit, so the two commit together or not at all.
The unit is of the call's tenant: another tenant's document is as missing as one never made.

No audit record is written: the mirror repeats what the channel recorded (and audited) in its own schema; the
document itself does not change.
"""

from dataclasses import dataclass
from typing import final

from ledger_invoicing.application.commands import tenant_of
from ledger_invoicing.domain.errors import InvoiceNotFoundError
from ledger_invoicing.domain.fiscal import Applied
from ledger_invoicing.events import InvoiceFiscalUpdated
lazy from ledger_invoicing.application.ports import InvoicingUnits
lazy from ledger_invoicing.domain.fiscal import FiscalStatus
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_platform.runtime.calls import AnyCall


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FiscalMirroring:
    """Applying a channel's statuses to the mirror, over the units it needs."""

    units: InvoicingUnits

    async def apply(self, call: AnyCall, document: InvoiceId, status: FiscalStatus) -> Applied:
        """Keep ``status`` for ``document`` if it is newer than the one kept for its channel (see the module).

        ``InvoiceNotFoundError`` when the call's tenant has no such document.
        """
        tenant = tenant_of(call)
        async with self.units.unit(tenant) as unit:
            applied = await unit.fiscal.apply(document, status)
            if applied is None:
                raise InvoiceNotFoundError
            if applied is Applied.APPLIED:
                unit.events.publish(
                    InvoiceFiscalUpdated(
                        scope=tenant,
                        subject=document,
                        aggregate_version=status.version,
                        channel=status.channel,
                        status=status.status.value,
                        version=status.version,
                    )
                )
        return applied
