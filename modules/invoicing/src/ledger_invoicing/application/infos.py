"""What other modules are told of a document (``api.InvoiceInfo``): data, never a domain object.

The facade answers it, and the ``IssuePolicy`` chain is handed it: the document as it will be issued (to
``check``) and as it was issued (to ``after_issue``).
"""

from types import MappingProxyType

from ledger_invoicing.api import (
    EstablishmentOfIssue,
    InvoiceInfo,
    LineInfo,
    LineTaxInfo,
    PartyInfo,
    SellerInfo,
)
from ledger_invoicing.domain.pricing import NO_FIELDS
lazy from ledger_invoicing.domain.invoice import Invoice
lazy from ledger_invoicing.domain.parties import Party
lazy from ledger_invoicing.domain.pricing import CountryFields, PricedLine


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
