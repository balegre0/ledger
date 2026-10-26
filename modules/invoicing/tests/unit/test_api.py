"""invoicing's API for other modules (§3.4): what a document looks like across it, and how it is bound.

What it reads is proven on PostgreSQL (``integration/test_facade.py``); here, the view a document becomes,
what its ``repr`` (and so a log line) may show of it, and that the implementation the assembly binds is
guarded.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
lazy from contextlib import AbstractAsyncContextManager

import pytest

from ledger_invoicing.api import (
    INVOICING_API,
    AuthorityMessageInfo,
    EstablishmentOfIssue,
    FiscalStatusInfo,
    FiscalStatusName,
    InvoiceInfo,
    LineInfo,
    LineTaxInfo,
    PartyInfo,
    SellerInfo,
)
from ledger_invoicing.application.facade import InvoicingFacade
from ledger_invoicing.application.infos import info_of
from ledger_invoicing.application.mirror import FiscalMirroring
from ledger_invoicing.application.queries import InvoicingQueries
from ledger_invoicing.domain.ids import InvoiceId
from ledger_invoicing.domain.invoice import Invoice, InvoiceStatus, Opening
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.parties import Seller, party
from ledger_invoicing.domain.pricing import Dating, EstablishmentRef, Terms, price
from ledger_invoicing.domain.text import parse_external_reference, parse_reason
from ledger_kernel.addresses import FiscalAddress
from ledger_kernel.ids import CompanyId, OrganizationId, new_id
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import Direction, DocumentKind, RegimeKey, TaxCategoryKey, TaxCategoryKind
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.facade import is_facade, require_facades
from ledger_platform.runtime.keys import BindingTypeError
lazy from ledger_invoicing.application.ports import InvoicingUnit
lazy from ledger_invoicing.domain.pricing import CountryFields
lazy from ledger_kernel.regimes import RegimeDef

NOW: Final = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)
TENANT: Final = Tenant(OrganizationId(new_id()), CompanyId(new_id()), Mode.LIVE)
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
ADDRESS: Final = FiscalAddress(line="Av. Duarte 10", municipality="010100", province="010000")
BUYER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "130000012")
SANTIAGO: Final = EstablishmentRef(code="SUC1", time_zone="America/Santo_Domingo")
WIDGET: Final = LineTerms(
    description="Widget for Juan Perez",
    quantity=Decimal("2.5"),
    unit_price=Decimal("40.10"),
    discount=Decimal("0.25"),
    taxes=("do.itbis.standard", "do.isc.standard"),
)
FIELDS: Final[CountryFields] = {"do": {"ecf_type": "31", "replaces": {"ncf": "B0100000123"}}}
TERMS: Final = Terms(
    currency=USD,
    lines=(WIDGET,),
    exchange_rate=Decimal("60.5"),
    issue_date=date(2026, 10, 10),
    tax_point=date(2026, 10, 1),
    establishment="SUC1",
    buyer=party(
        legal_name="Beta SRL",
        tax_id=BUYER_RNC,
        address="Calle 1, Santo Domingo",
        email="compras@beta.example",
    ),
    fiscal=FIELDS,
)


def issued(regime: RegimeDef, terms: Terms = TERMS, *, corrects: Invoice | None = None) -> Invoice:
    """An invoice of ``TENANT`` on ``terms`` (a credit note of ``corrects`` when given), issued at ``NOW``."""
    opening = Opening(
        id=InvoiceId(new_id()),
        tenant=TENANT,
        kind=DocumentKind.INVOICE if corrects is None else DocumentKind.CREDIT_NOTE,
        direction=Direction.SALES,
        external_reference=parse_external_reference("ORD-7"),
        terms=terms,
    )
    pricing = price(
        terms,
        regime=regime,
        functional_currency=DOP,
        establishment=SANTIAGO,
        dating=Dating(today=date(2026, 10, 12)),
    )
    invoice, _ = Invoice.new(
        opening,
        corrects=corrects,
        pricing=pricing,
        seller=Seller(legal_name="Acme SRL", tax_id=RNC, address=ADDRESS),
        now=NOW,
    )
    return invoice


def test_a_document_is_seen_whole_with_its_tenant_parties_lines_and_country_fields(
    regime_with_fields: RegimeDef,
) -> None:
    invoice = issued(regime_with_fields)
    pricing = invoice.pricing
    assert pricing.functional is not None
    view = info_of(invoice)
    assert view == InvoiceInfo(
        id=invoice.id,
        tenant=TENANT,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
        status=InvoiceStatus.ISSUED,
        version=1,
        external_reference="ORD-7",
        corrects=None,
        regime=RegimeKey("do"),
        establishment=EstablishmentOfIssue(code="SUC1", time_zone="America/Santo_Domingo"),
        currency=USD,
        exchange_rate=Decimal("60.5"),
        issue_date=date(2026, 10, 10),
        tax_point=date(2026, 10, 1),
        seller=SellerInfo(legal_name="Acme SRL", tax_id=RNC, address=ADDRESS),
        buyer=PartyInfo(
            legal_name="Beta SRL",
            tax_id=BUYER_RNC,
            address="Calle 1, Santo Domingo",
            email="compras@beta.example",
        ),
        lines=(
            LineInfo(
                description="Widget for Juan Perez",
                quantity=Decimal("2.5"),
                unit_price=Decimal("40.10"),
                discount=Decimal("0.25"),
                gross=Money.of("100.250", USD),
                base=Money.of("100.00", USD),
                taxes=(
                    LineTaxInfo(
                        category=TaxCategoryKey("do.itbis.standard"),
                        kind=TaxCategoryKind.STANDARD,
                        percent=Decimal(18),
                        since=date(2019, 1, 1),
                        amount=Money.of("18.00", USD),
                    ),
                    LineTaxInfo(
                        category=TaxCategoryKey("do.isc.standard"),
                        kind=TaxCategoryKind.STANDARD,
                        percent=Decimal(10),
                        since=date(2019, 1, 1),
                        amount=Money.of("10.00", USD),
                    ),
                ),
            ),
        ),
        totals=pricing.totals,
        functional_totals=pricing.functional,
        fields={"do": {"ecf_type": "31", "replaces": {"ncf": "B0100000123"}}},
        created_at=NOW,
        updated_at=NOW,
        issued_at=NOW,
        voided_at=None,
        void_reason=None,
    )
    assert view.livemode
    assert hash(view) == hash(replace(view, fields={}))  # the country fields are not hashed


def test_a_draft_has_no_seller_and_a_document_without_country_fields_names_no_regime_s(
    regime: RegimeDef,
) -> None:
    terms = Terms(currency=DOP, lines=(replace(WIDGET, taxes=("do.itbis.exempt",)),))
    opening = Opening(
        id=InvoiceId(new_id()),
        tenant=replace(TENANT, mode=Mode.TEST),
        kind=DocumentKind.INVOICE,
        direction=Direction.PURCHASE,
        external_reference=None,
        terms=terms,
    )
    pricing = price(
        terms, regime=regime, functional_currency=DOP, establishment=SANTIAGO, dating=Dating(today=NOW.date())
    )
    draft, _ = Invoice.new(opening, corrects=None, pricing=pricing, seller=None, now=NOW)
    view = info_of(draft)
    assert (view.status, view.seller, view.buyer, view.issued_at) == (InvoiceStatus.DRAFT, None, None, None)
    assert (view.tenant.mode, view.livemode, view.direction) == (Mode.TEST, False, Direction.PURCHASE)
    assert (view.currency, view.exchange_rate, view.functional_totals, view.fields) == (DOP, None, None, {})
    assert view.external_reference is None


def test_a_voided_note_names_the_invoice_it_corrects_and_why_it_was_voided(regime: RegimeDef) -> None:
    terms = replace(TERMS, currency=DOP, exchange_rate=None, fiscal={})
    invoice = issued(regime, terms)
    note = issued(regime, terms, corrects=invoice)
    later = NOW + timedelta(hours=1)
    voided, _ = note.void(
        reason=parse_reason("Wrong customer"), corrected_by_a_note=False, held=False, now=later
    )
    view = info_of(voided)
    assert (view.kind, view.corrects, view.status, view.version) == (
        DocumentKind.CREDIT_NOTE,
        invoice.id,
        InvoiceStatus.VOIDED,
        2,
    )
    assert (view.void_reason, view.voided_at, view.updated_at, view.created_at) == (
        "Wrong customer",
        later,
        later,
        NOW,
    )


def test_a_view_s_repr_masks_tax_ids_and_shows_nothing_a_client_wrote(regime_with_fields: RegimeDef) -> None:
    """A view is logged as its id, kind, status and version: never a name, an address, an email or a line."""
    view = info_of(issued(regime_with_fields))
    assert repr(view) == f"InvoiceInfo(id={view.id!r}, kind='invoice', status='issued', version=1)"
    assert view.seller is not None
    assert view.buyer is not None
    shown = " ".join(repr(part) for part in (view, view.seller, view.buyer, *view.lines))
    for secret in ("101850043", "130000012", "Acme", "Beta", "Calle", "compras@", "Juan", "ORD-7"):
        assert secret not in shown
    assert repr(view.seller) == f"SellerInfo(tax_id={RNC!r})"
    assert repr(view.buyer) == f"PartyInfo(tax_id={BUYER_RNC!r})"
    assert repr(view.lines[0]) == "LineInfo(taxes=('do.itbis.standard', 'do.isc.standard'))"


class _NoUnits:
    """Units nobody opens: the facade is only inspected here."""

    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[InvoicingUnit]:
        raise AssertionError(tenant)


def test_the_bound_api_is_guarded_and_known_by_its_key() -> None:
    units = _NoUnits()
    facade = InvoicingFacade(InvoicingQueries(units=units), FiscalMirroring(units=units))
    require_facades(facade)  # every entry point refuses a call made while a unit is open
    assert is_facade(InvoicingFacade.document)
    assert is_facade(InvoicingFacade.apply_fiscal_status)
    assert INVOICING_API.name == "invoicing"
    assert INVOICING_API.take(facade) is facade
    with pytest.raises(BindingTypeError):
        INVOICING_API.take(object())


def test_a_fiscal_status_s_repr_shows_its_channel_and_status_only() -> None:
    status = FiscalStatusInfo(
        channel="do-ecf-v1",
        environment="testecf",
        status=FiscalStatusName.REJECTED,
        authority_number="E310000000001",
        messages=(AuthorityMessageInfo(code="7", message="RNC del comprador 130000012 invalido", lang="es"),),
        regime="do",
        details={"encf": "E310000000001"},
        updated_at=NOW,
    )
    assert repr(status) == "FiscalStatusInfo(channel='do-ecf-v1', status='rejected')"
    assert "130000012" not in repr(status.messages)
