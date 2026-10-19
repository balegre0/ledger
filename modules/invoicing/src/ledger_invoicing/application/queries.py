"""What reads invoicing's state: a document of the call's tenant, and a page of its documents.

Each read opens one unit of the call's tenant (a company in a mode): row-level security and the repository
both keep it inside that tenant, so another company's or mode's document is not told apart from a missing one.
"""

from dataclasses import dataclass
from typing import final

from ledger_invoicing.application.commands import tenant_of
from ledger_invoicing.domain.errors import InvoiceNotFoundError
lazy from ledger_invoicing.application.ports import InvoiceFilters, InvoicingUnits
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.invoice import Invoice
lazy from ledger_kernel.pagination import Page, PageRequest
lazy from ledger_platform.runtime.calls import Call


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class InvoicingQueries:
    """The reads of documents, over the units they need."""

    units: InvoicingUnits

    async def document(self, call: Call, invoice: InvoiceId) -> Invoice:
        """The document ``invoice`` of the call's tenant; ``InvoiceNotFoundError`` otherwise."""
        async with self.units.unit(tenant_of(call)) as unit:
            found = await unit.invoices.get(invoice)
        if found is None:
            raise InvoiceNotFoundError
        return found

    async def documents(self, call: Call, request: PageRequest, filters: InvoiceFilters) -> Page[Invoice]:
        """The page ``request`` asks for of the tenant's documents that ``filters`` names, in id order."""
        async with self.units.unit(tenant_of(call)) as unit:
            return await unit.invoices.page(request, filters)
