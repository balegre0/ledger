"""``fiscal.certificates`` (§3.2, §8.6.4): a certificate installed moves what its company's channels parked.

The signer announces a certificate installed for a company and the modes it serves (``modes``: one or
both). For each of them, fiscal marks the company's enrollments in that mode not disabled ``review_due`` in a
unit of its own (Decision 34: what they require includes a certificate, reviewed at the next read), then,
outside any unit (rule 13), asks every channel the company is enrolled on in that mode, a disabled
enrollment's too (the documents routed through it still advance), to resume what waited for a certificate
(``Channel.resume(CERTIFICATE_INSTALLED)``); a channel no longer installed is skipped. Both steps are
idempotent, so a redelivery marks again and wakes nothing more. Each call is a ``SystemCall`` scoped to the
company in that mode, carrying the event's id (``call_id``, rule 4). The event carries nothing secret.
"""

from dataclasses import dataclass
from typing import Final, final
lazy from collections.abc import Sequence
lazy from uuid import UUID

from ledger_fiscal.application.routing import installed
from ledger_fiscal_kit.channels import ResumeReason
from ledger_kernel.tenancy import Tenant
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_fiscal.application.ports import FiscalUnits
lazy from ledger_fiscal_kit.channels import ChannelRegistry
lazy from ledger_kernel.tenancy import Mode

CERTIFICATES: Final = "fiscal.certificates"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Certificates:
    """``fiscal.certificates``'s use case (see the module)."""

    units: FiscalUnits
    channels: ChannelRegistry
    """The installed channels, read at call time."""

    async def installed(
        self, company: Tenant, modes: Sequence[Mode], *, event: UUID, traceparent: str | None
    ) -> None:
        """A certificate of ``company`` (a tenant without a mode) installed for ``modes`` (see the module)."""
        for mode in modes:
            tenant = Tenant(company.organization, company.company, mode)
            async with self.units.unit(tenant) as unit:
                enrolled = tuple(dict.fromkeys(each.channel for each in await unit.enrollments.every()))
                for key in enrolled:
                    await unit.enrollments.mark_due(key)
            call = SystemCall(reason=CERTIFICATES, scope=tenant, call_id=str(event), traceparent=traceparent)
            for key in enrolled:
                channel = installed(self.channels, key)
                if channel is not None:
                    await channel.resume(call, tenant.company, ResumeReason.CERTIFICATE_INSTALLED)
