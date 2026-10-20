"""What reads invoicing's state: a document of the call's tenant, and a page of its documents.

Each read opens one unit of the call's tenant (a company in a mode): row-level security and the repository
both keep it inside that tenant, so another company's or mode's document is not told apart from a missing one.
What the HTTP edge shows of a document is the document and its fiscal mirror (``Kept``), read in one unit.
"""

from dataclasses import dataclass
from typing import final

from ledger_invoicing.application.commands import tenant_of
from ledger_invoicing.domain.errors import InvoiceNotFoundError
from ledger_kernel.pagination import Page
lazy from ledger_invoicing.application.ports import InvoiceFilters, InvoicingUnits
lazy from ledger_invoicing.domain.fiscal import FiscalStatus
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.invoice import Invoice
lazy from ledger_kernel.pagination import PageRequest
lazy from ledger_platform.runtime.calls import AnyCall, Call


@final
@dataclass(frozen=True, slots=True)
class Kept:
    """A document as it is kept, with what each fiscal channel last reported of it (by channel key)."""

    invoice: Invoice
    fiscal: tuple[FiscalStatus, ...]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingQueries:
    """The reads of documents, over the units they need."""

    units: InvoicingUnits

    async def document(self, call: AnyCall, invoice: InvoiceId) -> Invoice:
        """The document ``invoice`` of the call's tenant; ``InvoiceNotFoundError`` otherwise.

        A request's call or the platform's (``InvoicingApi.document``): either names the tenant it reads.
        """
        async with self.units.unit(tenant_of(call)) as unit:
            found = await unit.invoices.get(invoice)
        if found is None:
            raise InvoiceNotFoundError
        return found

    async def kept(self, call: Call, invoice: InvoiceId) -> Kept:
        """The document ``invoice`` of the call's tenant and its fiscal mirror; ``InvoiceNotFoundError``."""
        async with self.units.unit(tenant_of(call)) as unit:
            found = await unit.invoices.get(invoice)
            if found is None:
                raise InvoiceNotFoundError
            mirrored = await unit.fiscal.of([found.id])
        return Kept(found, mirrored[found.id])

    async def documents(self, call: Call, request: PageRequest, filters: InvoiceFilters) -> Page[Kept]:
        """The page ``request`` asks for of the tenant's documents that ``filters`` names, in id order."""
        async with self.units.unit(tenant_of(call)) as unit:
            found = await unit.invoices.page(request, filters)
            mirrored = await unit.fiscal.of([invoice.id for invoice in found.items])
        return Page(
            items=tuple(Kept(invoice, mirrored[invoice.id]) for invoice in found.items),
            next_cursor=found.next_cursor,
            previous_cursor=found.previous_cursor,
            backwards=found.backwards,
        )
