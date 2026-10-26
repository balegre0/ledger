"""A ``FiscalDraft`` built from invoicing's document for an enrollment's channel (§8.4, §8.5 step 2).

The issuer is the document's seller (its primary tax id), the counterparty its buyer (``None`` without one, a
buyer without a tax id kept without one), the lines with their tax snapshots, the totals in the document's
currency and, for another currency, the exchange rate and the functional totals; a note refers to the invoice
it corrects (its id and its issue date); the fields are those of the regime's own namespace.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

import pytest

from ledger_fiscal.application.drafts import fiscal_draft, undeclared
from ledger_fiscal.domain.enrollments import Asked, Enrollment, EnrollmentId
from ledger_fiscal_kit.channels import Requirements
from ledger_fiscal_kit.drafts import DocumentRef, FiscalParty, FiscalReference
from ledger_invoicing.api import (
    EstablishmentOfIssue,
    InvoiceId,
    InvoiceInfo,
    InvoiceStatus,
    LineInfo,
    LineTaxInfo,
    PartyInfo,
    SellerInfo,
)
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import (
    Direction,
    DocumentKind,
    RegimeKey,
    TaxCalculator,
    TaxCategoryKey,
    TaxCategoryKind,
    TaxLine,
)
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode, Tenant
from ledger_platform.runtime.registries import Regimes
lazy from ledger_invoicing.api import CountryFields

REGIME: Final = Regimes.load().get("do")
PESO: Final = Currency.of("DOP")
DOLLAR: Final = Currency.of("USD")
STANDARD: Final = TaxCategoryKey("do.itbis.standard")
NOW: Final = datetime(2026, 10, 19, 12, tzinfo=UTC)
ISSUED_ON: Final = date(2026, 10, 19)
TENANT: Final = Tenant(
    OrganizationId(UUID("0199e3a0-0000-7000-8000-0000000000a1")),
    CompanyId(UUID("0199e3a0-0000-7000-8000-0000000000a2")),
    Mode.TEST,
)
INVOICE: Final = InvoiceId(UUID("0199e3a0-0000-7000-8000-0000000000a3"))
CORRECTED: Final = InvoiceId(UUID("0199e3a0-0000-7000-8000-0000000000a4"))
RNC: Final = TaxId(TaxIdScheme("do.rnc"), "101850043")
BUYER_RNC: Final = TaxId(TaxIdScheme("do.rnc"), "131098193")


def enrollment(tenant: Tenant = TENANT) -> Enrollment:
    asked = Asked(
        enrollment=EnrollmentId(UUID("0199e3a0-0000-7000-8000-0000000000a5")),
        tenant=tenant,
        channel="do-ecf-v1",
        environment="testecf",
    )
    return Enrollment.enroll(asked, requirements=Requirements(currently_due=()), now=NOW)


def view(currency: Currency = PESO) -> InvoiceInfo:
    """A sales invoice of one line of 1,000.00 at 18 %, priced as invoicing prices it."""
    base = Money(Decimal("1000.00"), currency)
    totals = TaxCalculator(REGIME).totals([TaxLine(base=base, categories=(STANDARD,))], ISSUED_ON)
    (category,) = totals.categories
    line = LineInfo(
        description="CEMENTO GRIS 42.5 KG",
        quantity=Decimal(2),
        unit_price=Decimal("500.00"),
        discount=Decimal(0),
        gross=base,
        base=base,
        taxes=(
            LineTaxInfo(
                category=STANDARD,
                kind=TaxCategoryKind.STANDARD,
                percent=category.percent,
                since=date(2019, 1, 1),
                amount=category.tax,
            ),
        ),
    )
    return InvoiceInfo(
        id=INVOICE,
        tenant=TENANT,
        kind=DocumentKind.INVOICE,
        direction=Direction.SALES,
        status=InvoiceStatus.ISSUED,
        version=3,
        external_reference="POS-1",
        corrects=None,
        regime=RegimeKey("do"),
        establishment=EstablishmentOfIssue(code="002", time_zone="America/Santo_Domingo"),
        currency=currency,
        exchange_rate=None,
        issue_date=ISSUED_ON,
        tax_point=ISSUED_ON,
        seller=SellerInfo(legal_name="FERRETERIA DUARTE SRL", tax_id=RNC),
        buyer=PartyInfo(
            legal_name="CONSTRUCTORA DEL CARIBE SRL",
            tax_id=BUYER_RNC,
            address="CALLE 5 #12, SANTIAGO",
            email="compras@example.test",
        ),
        lines=(line,),
        totals=totals,
        functional_totals=None,
        fields=MappingProxyType({}),
        created_at=NOW,
        updated_at=NOW,
        issued_at=NOW,
        voided_at=None,
        void_reason=None,
    )


def test_a_sales_invoice_becomes_the_channel_s_draft_of_its_enrollment() -> None:
    document = view()
    draft = fiscal_draft(document, enrollment=enrollment(), regime=REGIME)
    assert (draft.document, draft.version, draft.organization, draft.company, draft.mode) == (
        DocumentRef(INVOICE),
        3,
        TENANT.organization,
        TENANT.company,
        Mode.TEST,
    )
    assert (draft.channel, draft.environment, draft.regime, draft.kind, draft.direction) == (
        "do-ecf-v1",
        "testecf",
        "do",
        DocumentKind.INVOICE,
        Direction.SALES,
    )
    assert draft.issuer == FiscalParty(tax_id=RNC, legal_name="FERRETERIA DUARTE SRL")
    assert draft.counterparty == FiscalParty(
        tax_id=BUYER_RNC,
        legal_name="CONSTRUCTORA DEL CARIBE SRL",
        address="CALLE 5 #12, SANTIAGO",
        email="compras@example.test",
    )
    assert (draft.establishment_code, draft.time_zone) == ("002", "America/Santo_Domingo")
    (line,) = draft.lines
    (tax,) = line.taxes
    assert (line.number, line.description, line.quantity, line.unit_price, line.discount, line.goods) == (
        1,
        "CEMENTO GRIS 42.5 KG",
        Decimal(2),
        Decimal("500.00"),
        Decimal(0),
        None,
    )
    assert (tax.category, tax.kind, tax.percent, tax.since, tax.amount) == (
        STANDARD,
        TaxCategoryKind.STANDARD,
        Decimal(18),
        date(2019, 1, 1),
        Money(Decimal("180.00"), PESO),
    )
    assert (draft.totals, draft.currency, draft.exchange_rate, draft.functional_totals) == (
        document.totals,
        PESO,
        None,
        None,
    )
    assert (draft.references, dict(draft.fields), draft.issue_date, draft.tax_point) == (
        (),
        {},
        ISSUED_ON,
        ISSUED_ON,
    )
    assert draft.external_reference == "POS-1"


def test_a_buyer_without_a_tax_id_is_kept_without_one_and_no_buyer_is_no_counterparty() -> None:
    anonymous = PartyInfo(legal_name="CONSUMIDOR FINAL", tax_id=None, address=None, email=None)
    draft = fiscal_draft(replace(view(), buyer=anonymous), enrollment=enrollment(), regime=REGIME)
    assert draft.counterparty == FiscalParty(tax_id=None, legal_name="CONSUMIDOR FINAL")
    assert (
        fiscal_draft(replace(view(), buyer=None), enrollment=enrollment(), regime=REGIME).counterparty is None
    )


def test_a_credit_note_refers_to_the_invoice_it_corrects_by_its_id_and_its_issue_date() -> None:
    corrected = replace(view(), id=CORRECTED, issue_date=date(2026, 10, 1), tax_point=date(2026, 10, 1))
    note = replace(view(), kind=DocumentKind.CREDIT_NOTE, corrects=CORRECTED)
    draft = fiscal_draft(note, enrollment=enrollment(), regime=REGIME, corrected=corrected)
    assert draft.references == (
        FiscalReference(document=DocumentRef(CORRECTED), issue_date=date(2026, 10, 1)),
    )
    with pytest.raises(InvalidValueError, match="corrected"):
        fiscal_draft(note, enrollment=enrollment(), regime=REGIME)
    with pytest.raises(InvalidValueError, match="corrected"):
        fiscal_draft(view(), enrollment=enrollment(), regime=REGIME, corrected=corrected)
    with pytest.raises(InvalidValueError, match="corrected"):
        fiscal_draft(note, enrollment=enrollment(), regime=REGIME, corrected=view())


def test_a_document_in_another_currency_carries_its_rate_and_its_functional_totals() -> None:
    in_pesos = view().totals
    draft = fiscal_draft(
        replace(view(DOLLAR), exchange_rate=Decimal("60.50"), functional_totals=in_pesos),
        enrollment=enrollment(),
        regime=REGIME,
    )
    assert (draft.currency, draft.exchange_rate, draft.functional_totals) == (
        DOLLAR,
        Decimal("60.50"),
        in_pesos,
    )
    assert draft.totals.gross.currency == DOLLAR


def test_only_the_regime_s_own_fields_reach_the_draft_and_the_undeclared_ones_are_named() -> None:
    fields: CountryFields = MappingProxyType(
        {
            "do": MappingProxyType(
                {
                    "ecf_type": "31",
                    "income_type": "01",
                    "withholdings": MappingProxyType({"0": MappingProxyType({"itbis": "18.00"})}),
                }
            )
        }
    )
    document = replace(view(), fields=fields)
    draft = fiscal_draft(document, enrollment=enrollment(), regime=REGIME)
    assert dict(draft.fields) == {
        "ecf_type": "31",
        "income_type": "01",
        "withholdings": {"0": {"itbis": "18.00"}},
    }
    assert undeclared(document, REGIME) == ("income_type", "withholdings")
    assert undeclared(view(), REGIME) == ()


def test_a_document_that_could_be_no_draft_is_a_defect_of_its_caller() -> None:
    with pytest.raises(InvalidValueError, match="seller"):
        fiscal_draft(replace(view(), seller=None), enrollment=enrollment(), regime=REGIME)
    with pytest.raises(InvalidValueError, match="tenant"):
        fiscal_draft(
            replace(view(), tenant=Tenant(TENANT.organization, TENANT.company, None)),
            enrollment=enrollment(),
            regime=REGIME,
        )
    other = enrollment(
        Tenant(TENANT.organization, CompanyId(UUID("0199e3a0-0000-7000-8000-0000000000a6")), Mode.TEST)
    )
    with pytest.raises(InvalidValueError, match="enrollment"):
        fiscal_draft(view(), enrollment=other, regime=REGIME)
