"""A commercial document as a channel's input (§8.4 ``drafts``, §8.5 step 2).

invoicing's ``InvoiceInfo`` read into fiscal-kit's ``FiscalDraft``, for the channel and environment of one
enrollment.

The issuer is the document's seller as it was when it was issued (its primary tax id; its fiscal address, the
line with the codes of its municipality and province, or none if the company stated none), the counterparty
its buyer as the client sent it (``None`` when it names none; a buyer without a tax id keeps none); the
establishment's code and time zone, the lines with their tax snapshots, the totals, and for a document in
another currency than the company's the rate and the functional totals, all as invoicing priced them. A note
refers to the invoice it corrects, by its id and its issue date (``corrected``: invoicing's view names the id
only, so the caller reads that invoice first). The fields are those of the regime's own namespace
(``fiscal.<regime>``): another regime's are not this channel's.

A document that could be no draft (no seller, no mode, another tenant's enrollment, a note without the
invoice it corrects) is a defect of the caller (``InvalidValueError``): invoicing hands fiscal issued
documents of a company in a mode.

Not in the domain, as the plan placed it: the draft and the document are fiscal-kit's and invoicing's types,
which fiscal's domain (the kernel only) may not import.
"""

from ledger_fiscal_kit.drafts import (
    DocumentRef,
    FiscalDraft,
    FiscalLine,
    FiscalLineTax,
    FiscalParty,
    FiscalReference,
)
from ledger_kernel.errors import InvalidValueError
lazy from ledger_fiscal.domain.enrollments import Enrollment
lazy from ledger_fiscal_kit.drafts import FieldValue
lazy from ledger_invoicing.api import FieldValue as SentValue
lazy from ledger_invoicing.api import InvoiceInfo, LineInfo, PartyInfo, SellerInfo
lazy from ledger_kernel.regimes import RegimeDef


def _counterparty(buyer: PartyInfo | None) -> FiscalParty | None:
    if buyer is None:
        return None
    return FiscalParty(
        tax_id=buyer.tax_id, legal_name=buyer.legal_name, address=buyer.address, email=buyer.email
    )


def _issuer(seller: SellerInfo) -> FiscalParty:
    address = seller.address
    if address is None:
        return FiscalParty(tax_id=seller.tax_id, legal_name=seller.legal_name)
    return FiscalParty(
        tax_id=seller.tax_id,
        legal_name=seller.legal_name,
        address=address.line,
        municipality=address.municipality,
        province=address.province,
    )


def _line(number: int, line: LineInfo) -> FiscalLine:
    return FiscalLine(
        number=number,
        description=line.description,
        quantity=line.quantity,
        unit_price=line.unit_price,
        discount=line.discount,
        base=line.base,
        taxes=tuple(
            FiscalLineTax(
                category=tax.category, kind=tax.kind, percent=tax.percent, since=tax.since, amount=tax.amount
            )
            for tax in line.taxes
        ),
    )


def _value(sent: SentValue) -> FieldValue:
    """A country field's value as a draft holds it: text, or an object of such values."""
    if isinstance(sent, str):
        return sent
    return {name: _value(item) for name, item in sent.items()}


def _references(document: InvoiceInfo, corrected: InvoiceInfo | None) -> tuple[FiscalReference, ...]:
    """The invoice ``document`` corrects, read as ``corrected``; none for an invoice."""
    corrects = document.corrects
    if corrects is None and corrected is None:
        return ()
    if corrects is None or corrected is None or corrected.id != corrects:
        raise InvalidValueError(
            field="corrected", requirement="the invoice the document corrects, and only it"
        )
    return (FiscalReference(document=DocumentRef(corrected.id), issue_date=corrected.issue_date),)


def undeclared(document: InvoiceInfo, regime: RegimeDef) -> tuple[str, ...]:
    """The names of ``document``'s fields under ``fiscal.<regime>`` the regime does not declare, sorted.

    What a channel's format may read (Decision 24), and what nothing may when no channel routes the document.
    """
    declared = {definition.name for definition in regime.fields}
    return tuple(sorted(name for name in document.fields.get(regime.key, {}) if name not in declared))


def fiscal_draft(
    document: InvoiceInfo, *, enrollment: Enrollment, regime: RegimeDef, corrected: InvoiceInfo | None = None
) -> FiscalDraft:
    """``document`` as the draft of ``enrollment``'s channel and environment (see the module)."""
    tenant = document.tenant
    mode = tenant.mode
    if mode is None:
        raise InvalidValueError(field="document.tenant", requirement="a company in a mode")
    if enrollment.tenant != tenant:
        raise InvalidValueError(field="enrollment", requirement="the document's company, in its mode")
    seller = document.seller
    if seller is None:
        raise InvalidValueError(field="document.seller", requirement="set: the document is issued")
    sent = document.fields.get(regime.key, {})
    return FiscalDraft(
        document=DocumentRef(document.id),
        version=document.version,
        organization=tenant.organization,
        company=tenant.company,
        mode=mode,
        channel=enrollment.channel,
        environment=enrollment.environment,
        regime=regime.key,
        kind=document.kind,
        direction=document.direction,
        issuer=_issuer(seller),
        establishment_code=document.establishment.code,
        time_zone=document.establishment.time_zone,
        counterparty=_counterparty(document.buyer),
        lines=tuple(_line(number, line) for number, line in enumerate(document.lines, start=1)),
        totals=document.totals,
        currency=document.currency,
        exchange_rate=document.exchange_rate,
        functional_totals=document.functional_totals,
        references=_references(document, corrected),
        fields={name: _value(value) for name, value in sent.items()},
        issue_date=document.issue_date,
        tax_point=document.tax_point,
        external_reference=document.external_reference,
    )
