"""invoicing's API for the other modules (``api.InvoicingApi``), over its use cases.

Each method opens the unit its use case needs, in the tenant the call names, and gives back data
(``InvoiceInfo``), never a domain object. ``@facade`` refuses a call made while the caller holds a unit.
"""

from types import MappingProxyType
from typing import final, override

from ledger_invoicing.api import (
    EstablishmentOfIssue,
    InvoiceInfo,
    InvoicingApi,
    LineInfo,
    LineTaxInfo,
    PartyInfo,
    SellerInfo,
)
from ledger_invoicing.domain.pricing import NO_FIELDS
from ledger_platform.runtime.facade import facade
lazy from ledger_invoicing.application.queries import InvoicingQueries
lazy from ledger_invoicing.domain.ids import InvoiceId
lazy from ledger_invoicing.domain.invoice import Invoice
lazy from ledger_invoicing.domain.parties import Party
lazy from ledger_invoicing.domain.pricing import CountryFields, PricedLine
lazy from ledger_platform.runtime.calls import AnyCall


def _party(party: Party) -> PartyInfo:
    return PartyInfo(
        legal_name=party.legal_name, tax_id=party.tax_id, address=party.address, email=party.email
    )


def _line(line: PricedLine) -> LineInfo:
    terms = line.terms
    return LineInfo(
        description=terms.description,
        quantity=terms.quantity,
        unit_price=terms.unit_price,
        discount=terms.discount,
        gross=line.gross,
        base=line.base,
        taxes=tuple(
            LineTaxInfo(
                category=tax.category, kind=tax.kind, percent=tax.percent, since=tax.since, amount=tax.tax
            )
            for tax in line.taxes
        ),
    )


def info_of(invoice: Invoice) -> InvoiceInfo:
    """What other modules are told of ``invoice``."""
    terms, pricing, seller = invoice.terms, invoice.pricing, invoice.seller
    namespace: str = pricing.regime
    fields: CountryFields = MappingProxyType({namespace: pricing.fiscal}) if pricing.fiscal else NO_FIELDS
    return InvoiceInfo(
        id=invoice.id,
        tenant=invoice.tenant,
        kind=invoice.kind,
        direction=invoice.direction,
        status=invoice.status,
        version=invoice.version,
        external_reference=invoice.external_reference,
        corrects=invoice.corrects,
        regime=pricing.regime,
        establishment=EstablishmentOfIssue(
            code=pricing.establishment.code, time_zone=pricing.establishment.time_zone
        ),
        currency=terms.currency,
        exchange_rate=terms.exchange_rate,
        issue_date=pricing.issue_date,
        tax_point=pricing.tax_point,
        seller=None if seller is None else SellerInfo(legal_name=seller.legal_name, tax_id=seller.tax_id),
        buyer=None if terms.buyer is None else _party(terms.buyer),
        lines=tuple(_line(line) for line in pricing.lines),
        totals=pricing.totals,
        functional_totals=pricing.functional,
        fields=fields,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        issued_at=invoice.issued_at,
        voided_at=invoice.voided_at,
        void_reason=invoice.void_reason,
    )


@final
class InvoicingFacade(InvoicingApi):
    """The API, as invoicing implements it."""

    __slots__ = ("_queries",)

    def __init__(self, queries: InvoicingQueries) -> None:
        self._queries = queries

    @override
    @facade
    async def document(self, call: AnyCall, invoice: InvoiceId) -> InvoiceInfo:
        return info_of(await self._queries.document(call, invoice))
