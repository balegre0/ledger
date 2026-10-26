from dataclasses import replace
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Final
from uuid import UUID
lazy from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    MAX_LINES,
    BillingIndicator,
    Buyer,
    EcfReference,
    GoodsOrService,
    IncomeType,
    ModificationCode,
    PaymentType,
    WithholdingRole,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do.channels.ecf_v1.domain.mapping import (
    DraftObjectedError,
    NumberTypeError,
    TotalsDisagreeError,
    ecf_draft,
    ecf_type_of,
    objections,
    verify_totals,
)
from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
from ledger_country_do.channels.ecf_v1.domain.totals import RateGroup, compute_totals
from ledger_country_do_testing.fiscal import (
    BUYER,
    EXEMPT,
    ISSUER,
    REDUCED,
    STANDARD,
    ZERO,
    Priced,
    fiscal_draft,
)
from ledger_fiscal_kit.drafts import DocumentRef, FiscalLineTax, FiscalParty, FiscalReference
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import (
    CategoryTotal,
    Direction,
    DocumentKind,
    TaxCategoryKey,
    TaxCategoryKind,
)
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
lazy from ledger_fiscal_kit.drafts import FieldValue, FiscalDraft
lazy from ledger_fiscal_kit.objections import Objection
lazy from ledger_kernel.regimes import Totals

D = Decimal
INVOICE, CREDIT, DEBIT = DocumentKind.INVOICE, DocumentKind.CREDIT_NOTE, DocumentKind.DEBIT_NOTE
SALES, PURCHASE = Direction.SALES, Direction.PURCHASE
CORRECTED: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-0000000000e1"))
CORRECTS: Final = (FiscalReference(document=CORRECTED, issue_date=date(2026, 10, 1)),)
ACCEPTED: Final = {CORRECTED: AcceptedEcf(encf=Encf(EcfType.CREDIT_INVOICE, 9), issued_on=date(2026, 10, 1))}
NONE_ACCEPTED: Final[Mapping[DocumentRef, AcceptedEcf]] = {}
USD: Final = Currency.of("USD")
SECRET: Final = "SECRETO-9876"
PASSPORT: Final = TaxId(TaxIdScheme("us.passport"), "P12345678")
ANONYMOUS: Final = FiscalParty(tax_id=None, legal_name=None)
PAYEE: Final = FiscalParty(tax_id=PASSPORT, legal_name="ACME INC")
NUMBER: Final = Encf(EcfType.CREDIT_INVOICE, 1)
EXPIRES_ON: Final = date(2028, 12, 31)


def found(
    draft: FiscalDraft, accepted: Mapping[DocumentRef, AcceptedEcf] = NONE_ACCEPTED
) -> list[tuple[str, str | None]]:
    return [(each.code, each.field) for each in objections(draft, accepted=accepted)]


def note(kind: DocumentKind, **fields: str) -> FiscalDraft:
    return fiscal_draft(kind=kind, references=CORRECTS, fields={"modification_code": "3", **fields})


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        (fiscal_draft(), EcfType.CREDIT_INVOICE),
        (
            fiscal_draft(counterparty=FiscalParty(tax_id=None, legal_name="JUAN PEREZ")),
            EcfType.CONSUMER_INVOICE,
        ),
        (fiscal_draft(counterparty=None), EcfType.CONSUMER_INVOICE),
        (
            fiscal_draft(counterparty=FiscalParty(tax_id=PASSPORT, legal_name="JOHN DOE")),
            EcfType.CONSUMER_INVOICE,
        ),
        (note(CREDIT), EcfType.CREDIT_NOTE),
        (note(DEBIT), EcfType.DEBIT_NOTE),
        (fiscal_draft(direction=PURCHASE), None),
        (fiscal_draft(direction=PURCHASE, fields={"ecf_type": "41"}), EcfType.PURCHASES),
        (fiscal_draft(direction=PURCHASE, fields={"ecf_type": "47"}), EcfType.FOREIGN_PAYMENTS),
        (fiscal_draft(fields={"ecf_type": "45"}), EcfType.GOVERNMENT),
    ],
    ids=[
        "invoice-buyer-rnc",
        "invoice-buyer-unidentified",
        "invoice-no-buyer",
        "invoice-buyer-foreign-id",
        "credit-note",
        "debit-note",
        "purchase-unnamed",
        "purchase-41",
        "purchase-47",
        "government-45",
    ],
)
def test_the_default_e_cf_type_of_each_document(draft: FiscalDraft, expected: EcfType | None) -> None:
    assert ecf_type_of(draft) is expected


def test_a_valid_draft_has_no_objection() -> None:
    assert found(fiscal_draft()) == []
    assert found(note(CREDIT), ACCEPTED) == []
    minor = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT),
        direction=PURCHASE,
        counterparty=None,
        fields={"ecf_type": "43"},
    )
    assert found(minor) == []


def test_a_type_s_comprador_takes_what_it_holds_and_no_rnc_on_a_foreign_payment() -> None:
    supplier = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT), direction=PURCHASE, fields={"ecf_type": "43"}
    )
    assert found(supplier) == []
    minor = ecf_draft(
        supplier, number=Encf(EcfType.MINOR_EXPENSES, 1), accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON
    )
    assert minor.buyer.anonymous
    payee = FiscalParty(tax_id=PASSPORT, legal_name="ACME INC", address="1 MAIN ST, MIAMI")
    abroad = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT),
        direction=PURCHASE,
        counterparty=payee,
        fields=_typed("47"),
    )
    paid = ecf_draft(
        abroad, number=Encf(EcfType.FOREIGN_PAYMENTS, 1), accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON
    )
    assert paid.buyer == Buyer(foreign_id="P12345678", name="ACME INC")
    local = replace(abroad, counterparty=BUYER)
    assert found(local) == [("do_ecf_buyer_tax_id_not_allowed", "buyer.tax_id")]


def test_a_field_a_type_does_not_hold_is_invalid_on_it() -> None:
    purchase = fiscal_draft(direction=PURCHASE, fields={"ecf_type": "41", "income_type": "01"})
    assert found(purchase) == [("do_ecf_field_invalid", "fiscal.do.income_type")]
    minor = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT),
        direction=PURCHASE,
        counterparty=None,
        fields={"ecf_type": "43", "payment_due": "2026-11-12"},
    )
    assert found(minor) == [("do_ecf_field_invalid", "fiscal.do.payment_due")]


def test_china_s_yuan_is_not_dgii_s_chy() -> None:
    # DGII's TipoMoneda lists "CHY", which is no ISO 4217 code: the yuan (CNY) cannot be written.
    yuan = fiscal_draft(currency=Currency.of("CNY"), exchange_rate=D("8.1"))
    assert found(yuan) == [("do_ecf_currency_unsupported", "currency")]


def test_a_purchase_names_its_type() -> None:
    assert found(fiscal_draft(direction=PURCHASE)) == [("do_ecf_ecf_type_required", "fiscal.do.ecf_type")]


@pytest.mark.parametrize(
    "draft",
    [
        note(CREDIT, ecf_type="31"),
        fiscal_draft(fields={"ecf_type": "41"}),
        fiscal_draft(direction=PURCHASE, fields={"ecf_type": "31"}),
        note(DEBIT, ecf_type="34"),
        fiscal_draft(direction=PURCHASE, kind=CREDIT, references=CORRECTS, fields={"modification_code": "1"}),
    ],
)
def test_a_type_of_another_kind_or_direction_is_not_allowed(draft: FiscalDraft) -> None:
    assert found(draft, ACCEPTED) == [("do_ecf_ecf_type_not_allowed", "fiscal.do.ecf_type")]


def test_an_unreadable_type_is_only_invalid() -> None:
    assert found(fiscal_draft(fields={"ecf_type": "30"})) == [("do_ecf_field_invalid", "fiscal.do.ecf_type")]


@pytest.mark.parametrize(
    ("draft", "field"),
    [
        (
            fiscal_draft(
                counterparty=FiscalParty(tax_id=None, legal_name="JUAN PEREZ"), fields={"ecf_type": "31"}
            ),
            "buyer.tax_id",
        ),
        (
            fiscal_draft(
                counterparty=FiscalParty(tax_id=PASSPORT, legal_name="X"), fields={"ecf_type": "31"}
            ),
            "buyer.tax_id",
        ),
        (
            fiscal_draft(
                direction=PURCHASE, counterparty=replace(BUYER, tax_id=None), fields={"ecf_type": "41"}
            ),
            "buyer.tax_id",
        ),
        (fiscal_draft(counterparty=replace(BUYER, tax_id=None), fields={"ecf_type": "45"}), "buyer.tax_id"),
        (fiscal_draft(Priced(unit_price=D("211864.41")), counterparty=ANONYMOUS), "buyer.tax_id"),
    ],
    ids=["31-unidentified", "31-foreign", "41", "45", "32-at-the-threshold"],
)
def test_a_type_or_an_amount_that_requires_it_names_the_buyer_s_rnc(draft: FiscalDraft, field: str) -> None:
    assert found(draft) == [("do_ecf_buyer_tax_id_required", field)]


def test_a_consumer_invoice_below_the_threshold_may_be_anonymous() -> None:
    assert found(fiscal_draft(Priced(unit_price=D("211864.40")), counterparty=ANONYMOUS)) == []
    pesos = fiscal_draft(
        Priced(unit_price=D("3606.00")), counterparty=ANONYMOUS, currency=USD, exchange_rate=D("58.7512")
    )
    assert found(pesos) == []


@pytest.mark.parametrize("ecf_type", ["44", "46"])
def test_a_type_that_requires_it_names_the_buyer(ecf_type: str) -> None:
    unnamed = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT if ecf_type == "44" else ZERO),
        counterparty=replace(BUYER, legal_name=None),
        fields={"ecf_type": ecf_type},
    )
    assert found(unnamed) == [("do_ecf_buyer_name_required", "buyer.legal_name")]


def test_a_note_names_why_it_corrects_and_corrects_one_document() -> None:
    unexplained = fiscal_draft(kind=CREDIT, references=CORRECTS)
    assert found(unexplained, ACCEPTED) == [
        ("do_ecf_modification_code_required", "fiscal.do.modification_code")
    ]
    twice = replace(note(DEBIT), references=CORRECTS * 2)
    assert found(twice, ACCEPTED) == [("do_ecf_reference_required", "corrects")]
    contingency = note(CREDIT, modification_code="4")
    assert found(contingency, ACCEPTED) == [("do_ecf_field_invalid", "fiscal.do.modification_code")]


def test_an_invoice_takes_no_modification_code() -> None:
    assert found(fiscal_draft(fields={"modification_code": "1"})) == [
        ("do_ecf_field_invalid", "fiscal.do.modification_code")
    ]


def test_a_document_holds_as_many_lines_as_its_type_allows() -> None:
    many = fiscal_draft()
    lines = tuple(replace(many.lines[0], number=number) for number in range(1, MAX_LINES + 2))
    assert found(replace(many, lines=lines)) == [("do_ecf_line_count", "lines")]
    assert found(replace(note(CREDIT), lines=lines), ACCEPTED) == []


def test_a_foreign_currency_is_one_dgii_lists_with_its_exchange_rate_to_pesos() -> None:
    foreign = fiscal_draft(currency=USD, exchange_rate=D("58.7512"))
    assert found(foreign) == []
    assert found(replace(foreign, exchange_rate=None, functional_totals=None)) == [
        ("do_ecf_exchange_rate_required", "exchange_rate")
    ]
    euros = fiscal_draft(currency=Currency.of("EUR"), exchange_rate=D("1.08"))
    assert found(replace(euros, functional_totals=_in(USD, euros))) == [
        ("do_ecf_exchange_rate_required", "exchange_rate")
    ]
    assert found(replace(foreign, exchange_rate=D("58.75125"))) == [
        ("do_ecf_exchange_rate_invalid", "exchange_rate")
    ]
    assert found(fiscal_draft(currency=Currency.of("ARS"), exchange_rate=D("0.06"))) == [
        ("do_ecf_currency_unsupported", "currency")
    ]
    special = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT),
        currency=USD,
        exchange_rate=D(58),
        fields={"ecf_type": "44"},
    )
    assert found(special) == []


def _in(currency: Currency, draft: FiscalDraft) -> Totals:
    functional = draft.functional_totals
    assert functional is not None
    return replace(
        functional,
        net=Money(functional.net.amount, currency),
        categories=tuple(
            replace(each, base=Money(each.base.amount, currency), tax=Money(each.tax.amount, currency))
            for each in functional.categories
        ),
        tax=Money(functional.tax.amount, currency),
        gross=Money(functional.gross.amount, currency),
    )


@pytest.mark.parametrize(
    ("ecf_type", "category"),
    [("43", STANDARD), ("44", REDUCED), ("47", ZERO), ("46", STANDARD), ("46", REDUCED), ("46", EXEMPT)],
)
def test_a_line_taxed_as_its_type_cannot_be_is_not_allowed(ecf_type: str, category: TaxCategoryKey) -> None:
    direction = PURCHASE if ecf_type in {"43", "47"} else SALES
    draft = fiscal_draft(
        Priced(unit_price=D(100), category=category),
        direction=direction,
        counterparty=PAYEE if ecf_type == "47" else BUYER,
        fields=_typed(ecf_type),
    )
    assert found(draft) == [("do_ecf_category_not_allowed", "lines.0.taxes")]


@pytest.mark.parametrize("ecf_type", ["43", "47"])
def test_minor_expenses_and_foreign_payments_take_no_discount(ecf_type: str) -> None:
    draft = fiscal_draft(
        Priced(unit_price=D(100), discount=D(1), category=EXEMPT),
        direction=PURCHASE,
        counterparty=PAYEE if ecf_type == "47" else BUYER,
        fields=_typed(ecf_type),
    )
    assert found(draft) == [("do_ecf_discount_not_allowed", "lines.0.discount")]


def test_line_rules_are_dgii_s_tighter_limits() -> None:
    draft = fiscal_draft(
        Priced(unit_price=D(10), quantity=D("1.005")),
        Priced(unit_price=D("10.00001")),
        Priced(unit_price=D(10), discount=D("0.001")),
        Priced(unit_price=D(10), description="X" * 81),
        Priced(unit_price=D(10), description="CEMENTO\tGRIS"),
    )
    assert found(draft) == [
        ("do_ecf_quantity_precision", "lines.0.quantity"),
        ("do_ecf_unit_price_precision", "lines.1.unit_price"),
        ("do_ecf_discount_precision", "lines.2.discount"),
        ("do_ecf_text_too_long", "lines.3.description"),
        ("do_ecf_text_invalid", "lines.4.description"),
    ]


def test_amounts_have_at_most_sixteen_integer_digits() -> None:
    huge = fiscal_draft(Priced(unit_price=D("9000000000000000.00"), category=EXEMPT, quantity=D(2)))
    assert found(huge) == [("do_ecf_amount_too_large", "totals"), ("do_ecf_amount_too_large", "lines.0")]
    pesos = fiscal_draft(
        Priced(unit_price=D("9000000000000000.00"), category=EXEMPT),
        counterparty=BUYER,
        currency=USD,
        exchange_rate=D(2),
    )
    assert found(pesos) == [("do_ecf_amount_too_large", "totals")]
    zero = fiscal_draft(Priced(unit_price=D(10), quantity=D(0)))
    assert found(zero) == [("do_ecf_number_invalid", "lines.0.quantity")]


def test_a_tax_the_channel_cannot_bill_is_objected() -> None:
    base = fiscal_draft()
    line = base.lines[0]
    isc = FiscalLineTax(
        category=TaxCategoryKey("do.isc.alcohol"),
        kind=TaxCategoryKind.STANDARD,
        percent=D(10),
        since=date(2019, 1, 1),
        amount=Money.of("100.00", base.currency),
    )
    tip = replace(isc, category=TaxCategoryKey("do.tip.legal"))
    cases = {
        (isc,): "do_ecf_isc_unsupported",
        (*line.taxes, isc): "do_ecf_isc_unsupported",
        (tip,): "do_ecf_category_unmapped",
        (*line.taxes, *line.taxes): "do_ecf_category_unmapped",
        (): "do_ecf_category_unmapped",
    }
    for taxes, code in cases.items():
        assert found(replace(base, lines=(replace(line, taxes=taxes),))) == [(code, "lines.0.taxes")]


def test_the_parties_texts_and_ids_are_dgii_s() -> None:
    draft = fiscal_draft(
        issuer=replace(ISSUER, legal_name=None, address="X" * 101),
        counterparty=FiscalParty(
            tax_id=TaxId(TaxIdScheme("us.passport"), "P" * 21), legal_name="X" * 151, address="CALLE\n5"
        ),
    )
    assert found(draft) == [
        ("do_ecf_text_invalid", "buyer.address"),
        ("do_ecf_text_too_long", "buyer.legal_name"),
        ("do_ecf_text_too_long", "buyer.tax_id"),
        ("do_ecf_text_too_long", "seller.address"),
        ("do_ecf_value_required", "seller.legal_name"),
    ]
    foreign_seller = fiscal_draft(issuer=replace(ISSUER, tax_id=PASSPORT, address=None))
    assert found(foreign_seller) == [
        ("do_ecf_company_address_missing", "seller.address"),
        ("do_ecf_tax_id_invalid", "seller.tax_id"),
    ]


def test_a_company_that_states_no_fiscal_address_is_told_to_state_it_on_the_company() -> None:
    """Task 35: ``DireccionEmisor`` is required by every e-CF; the client cannot send it with the invoice."""
    (objection,) = objections(fiscal_draft(issuer=replace(ISSUER, address=None)), accepted=NONE_ACCEPTED)
    assert (objection.code, objection.field) == ("do_ecf_company_address_missing", "seller.address")
    assert objection.message == (
        "seller.address is the company's fiscal address as DGII has it on record: state it on the company "
        "(PATCH /v1/companies/{id}, address)"
    )


def test_a_municipality_or_province_outside_dgii_s_catalogue_is_objected() -> None:
    located = replace(ISSUER, municipality="010100", province="010000")
    assert found(fiscal_draft(issuer=located)) == []
    unknown = replace(ISSUER, municipality="999999", province="01")
    assert found(fiscal_draft(issuer=unknown)) == [
        ("do_ecf_code_unknown", "seller.municipality"),
        ("do_ecf_code_unknown", "seller.province"),
    ]
    assert all(
        "999999" not in each.message
        for each in objections(fiscal_draft(issuer=unknown), accepted=NONE_ACCEPTED)
    )


def test_an_e_cf_draft_names_the_issuer_s_municipality_and_province_when_the_company_states_them() -> None:
    located = fiscal_draft(issuer=replace(ISSUER, municipality="010100", province="010000"))
    drafted = ecf_draft(located, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON)
    assert (drafted.issuer.address, drafted.issuer.municipality, drafted.issuer.province) == (
        "AV. DUARTE 100, SANTO DOMINGO",
        "010100",
        "010000",
    )
    plain = ecf_draft(fiscal_draft(), number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON)
    assert (plain.issuer.municipality, plain.issuer.province) == (None, None)


def test_several_problems_are_all_returned_by_line_then_field_and_never_quote_a_value() -> None:
    draft = fiscal_draft(
        Priced(unit_price=D(10), description=SECRET * 10),
        Priced(unit_price=D(10), quantity=D("1.005"), description=SECRET * 10),
        counterparty=replace(BUYER, legal_name=SECRET * 20),
        fields={"colour": SECRET, "ecf_type": "31"},
    )
    result = objections(draft, accepted=NONE_ACCEPTED)
    assert [(each.code, each.field) for each in result] == [
        ("do_ecf_text_too_long", "buyer.legal_name"),
        ("do_ecf_field_unknown", "fiscal.do.colour"),
        ("do_ecf_text_too_long", "lines.0.description"),
        ("do_ecf_text_too_long", "lines.1.description"),
        ("do_ecf_quantity_precision", "lines.1.quantity"),
    ]
    assert all(SECRET not in each.message and "1.005" not in each.message for each in result)
    assert all(each.message.startswith(f"{each.field} ") for each in result)


def _typed(ecf_type: str) -> dict[str, FieldValue]:
    """The fields naming ``ecf_type``, with the ISR a payment abroad (47) withholds on its one line."""
    if ecf_type == "47":
        return {"ecf_type": ecf_type, "withholdings": {"0": {"isr": "0.00"}}}
    return {"ecf_type": ecf_type}


def _codes(found_objections: tuple[Objection, ...]) -> list[str]:
    return [each.code for each in found_objections]


def test_an_e_cf_draft_carries_the_document_as_dgii_writes_it() -> None:
    draft = fiscal_draft(
        Priced(unit_price=D("1500.00"), quantity=D(2), discount=D("10.00"), goods=False),
        Priced(unit_price=D("120.3333"), quantity=D(3), category=EXEMPT, goods=True),
        Priced(unit_price=D(100), category=ZERO),
        Priced(unit_price=D(100), category=REDUCED),
        fields={"income_type": "02", "payment_type": "2", "payment_due": "2026-11-12"},
    )
    drafted = ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON)
    assert (drafted.encf, drafted.issued_on, drafted.sequence_expires_on) == (
        NUMBER,
        date(2026, 10, 13),
        EXPIRES_ON,
    )
    assert (drafted.issuer.rnc, drafted.issuer.legal_name, drafted.issuer.address) == (
        "101850043",
        "FERRETERIA DUARTE SRL",
        "AV. DUARTE 100, SANTO DOMINGO",
    )
    assert drafted.buyer == Buyer(
        rnc="131098193", name="CONSTRUCTORA DEL CARIBE SRL", address="CALLE 5 #12, SANTIAGO"
    )
    assert [(line.billing, line.kind, line.discount) for line in drafted.lines] == [
        (BillingIndicator.ITBIS_1, GoodsOrService.SERVICE, D("10.00")),
        (BillingIndicator.EXEMPT, GoodsOrService.GOODS, D(0)),
        (BillingIndicator.ITBIS_3, GoodsOrService.GOODS, D(0)),
        (BillingIndicator.ITBIS_2, GoodsOrService.GOODS, D(0)),
    ]
    assert (drafted.income, drafted.payment, drafted.payment_due) == (
        IncomeType.FINANCIAL,
        PaymentType.CREDIT,
        date(2026, 11, 12),
    )
    assert (drafted.references, drafted.currency, drafted.exchange_rate) == ((), draft.currency, None)
    verify_totals(draft, compute_totals(drafted))


def test_an_e_cf_draft_of_a_note_carries_its_reference_and_of_a_foreign_buyer_its_identifier() -> None:
    drafted = ecf_draft(note(CREDIT), number=Encf(EcfType.CREDIT_NOTE, 1), accepted=ACCEPTED, expires_on=None)
    assert drafted.references == (
        EcfReference(
            encf=Encf(EcfType.CREDIT_INVOICE, 9),
            issued_on=date(2026, 10, 1),
            code=ModificationCode.CORRECT_AMOUNTS,
        ),
    )
    foreign = fiscal_draft(
        counterparty=FiscalParty(tax_id=PASSPORT, legal_name="JOHN DOE"),
        currency=USD,
        exchange_rate=D("58.75"),
    )
    consumer = ecf_draft(
        foreign, number=Encf(EcfType.CONSUMER_INVOICE, 1), accepted=NONE_ACCEPTED, expires_on=None
    )
    assert consumer.buyer == Buyer(foreign_id="P12345678", name="JOHN DOE")
    assert (consumer.currency, consumer.exchange_rate) == (USD, D("58.75"))
    anonymous = ecf_draft(
        fiscal_draft(counterparty=None),
        number=Encf(EcfType.CONSUMER_INVOICE, 1),
        accepted=NONE_ACCEPTED,
        expires_on=None,
    )
    assert anonymous.buyer.anonymous


def test_an_objected_draft_or_a_number_of_another_type_drafts_no_e_cf() -> None:
    with pytest.raises(
        DraftObjectedError, match=r"do_ecf_ecf_type_required at fiscal\.do\.ecf_type"
    ) as caught:
        ecf_draft(
            fiscal_draft(direction=PURCHASE), number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON
        )
    assert (caught.value.kind, caught.value.code) == ("invalid", "do_ecf_draft_objected")
    assert _codes(caught.value.objections) == ["do_ecf_ecf_type_required"]
    with pytest.raises(NumberTypeError) as wrong:
        ecf_draft(
            fiscal_draft(), number=Encf(EcfType.CONSUMER_INVOICE, 1), accepted=NONE_ACCEPTED, expires_on=None
        )
    assert (wrong.value.kind, wrong.value.code) == ("internal", "do_ecf_number_type")


def test_the_kernel_s_totals_and_dgii_s_agree_within_the_tolerance() -> None:
    draft = fiscal_draft(Priced(unit_price=D(100)), Priced(unit_price=D(50), category=EXEMPT))
    totals = compute_totals(ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    verify_totals(draft, totals)
    verify_totals(draft, replace(totals, total=totals.total + D(2)))
    with pytest.raises(TotalsDisagreeError, match="gross") as caught:
        verify_totals(draft, replace(totals, total=totals.total + D(3)))
    assert (caught.value.kind, caught.value.code) == ("internal", "do_ecf_totals_disagree")
    assert "171" not in str(caught.value)
    taxed_apart = replace(draft.totals, tax=draft.totals.tax + Money.of("3.00", draft.currency))
    with pytest.raises(TotalsDisagreeError, match="tax"):
        verify_totals(replace(draft, totals=taxed_apart), totals)
    group = totals.groups[0]
    for changed, what in [
        (replace(totals, groups=(replace(group, base=group.base + D(3)),)), "do.itbis.standard"),
        (replace(totals, groups=(replace(group, itbis=group.itbis - D(3)),)), "do.itbis.standard"),
        (replace(totals, groups=()), "do.itbis.standard"),
        (replace(totals, exempt=None), "do.itbis.exempt"),
        (replace(totals, exempt=D("53.01")), "do.itbis.exempt"),
        (
            replace(totals, groups=(group, RateGroup(BillingIndicator.ITBIS_2, D(0), D(16), D(0)))),
            "categories",
        ),
    ]:
        with pytest.raises(TotalsDisagreeError, match=what):
            verify_totals(draft, changed)


def test_a_line_s_tolerance_is_one_unit() -> None:
    draft = fiscal_draft(Priced(unit_price=D(100)), Priced(unit_price=D(100)))
    totals = compute_totals(ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    verify_totals(draft, replace(totals, total=totals.total + D(2)))
    with pytest.raises(TotalsDisagreeError):
        verify_totals(draft, replace(totals, total=totals.total + D("2.01")))


def test_a_foreign_document_s_totals_agree_in_both_currencies() -> None:
    draft = fiscal_draft(
        Priced(unit_price=D("33.33"), quantity=D(3)), currency=USD, exchange_rate=D("58.7512")
    )
    totals = compute_totals(ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    verify_totals(draft, totals)
    foreign = totals.foreign
    assert foreign is not None
    with pytest.raises(TotalsDisagreeError, match="document's currency"):
        verify_totals(draft, replace(totals, foreign=replace(foreign, total=foreign.total + D(5))))
    with pytest.raises(TotalsDisagreeError, match="pesos"):
        verify_totals(draft, replace(totals, total=totals.total + D(5)))
    with pytest.raises(TotalsDisagreeError, match="document's currency"):
        verify_totals(draft, replace(totals, foreign=None))
    with pytest.raises(TotalsDisagreeError, match="pesos"):
        verify_totals(replace(draft, exchange_rate=None, functional_totals=None), totals)
    pesos = fiscal_draft(Priced(unit_price=D("33.33"), quantity=D(3)))
    with pytest.raises(TotalsDisagreeError, match="pesos"):
        verify_totals(pesos, totals)
    own = compute_totals(ecf_draft(pesos, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    with pytest.raises(TotalsDisagreeError, match="document's currency"):
        verify_totals(pesos, replace(own, foreign=own))


def test_a_category_dgii_does_not_bill_never_agrees() -> None:
    draft = fiscal_draft()
    totals = compute_totals(ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    tip = CategoryTotal(
        category=TaxCategoryKey("do.tip.legal"),
        percent=D(10),
        base=Money.of("0", draft.currency),
        tax=Money.of("0", draft.currency),
    )
    odd = replace(draft, totals=replace(draft.totals, categories=(*draft.totals.categories, tip)))
    with pytest.raises(TotalsDisagreeError, match=r"do\.tip\.legal"):
        verify_totals(odd, totals)


_CATEGORIES: Final = (STANDARD, REDUCED, ZERO, EXEMPT)


@st.composite
def _priced(draw: st.DrawFn) -> Priced:
    """A line of any of the four ITBIS categories, its discount at most its gross."""
    unit_price = draw(st.decimals(min_value=D(0), max_value=D(99999), places=4))
    quantity = draw(st.decimals(min_value=D("0.01"), max_value=D(9999), places=2))
    gross = (unit_price * quantity).quantize(D("0.01"), rounding=ROUND_DOWN)
    discount = draw(st.decimals(min_value=D(0), max_value=gross, places=2))
    category = draw(st.sampled_from(_CATEGORIES))
    return Priced(unit_price=unit_price, quantity=quantity, discount=discount, category=category)


_PRICED: Final = _priced()


@given(st.lists(_PRICED, min_size=1, max_size=20), st.sampled_from([Currency.of("DOP"), USD]))
def test_the_kernel_s_calculator_totals_every_document_as_dgii_does(
    lines: list[Priced], currency: Currency
) -> None:
    # §16.4: the golden-file promise as a property. Invoicing totals with the kernel's calculator; the channel
    # computes DGII's totals from the e-CF draft; at DGII's rounding points they are equal, not merely close.
    rate = None if currency == Currency.of("DOP") else D("58.7512")
    draft = fiscal_draft(*lines, currency=currency, exchange_rate=rate)
    totals = compute_totals(ecf_draft(draft, number=NUMBER, accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON))
    verify_totals(draft, totals)
    own = totals.foreign or totals
    by_category = {each.category: each for each in draft.totals.categories}
    for group in own.groups:
        kernel = by_category.pop(TaxCategoryKey(f"do.itbis.{_WORD[group.indicator]}"))
        assert (kernel.base.amount, kernel.tax.amount) == (group.base, group.itbis)
    exempt = by_category.pop(EXEMPT, None)
    assert by_category == {}
    assert (exempt.base.amount if exempt is not None else None) == own.exempt
    assert draft.totals.gross.amount == own.total
    assert draft.totals.tax.amount == (own.itbis_total or 0)


_WORD: Final = {
    BillingIndicator.ITBIS_1: "standard",
    BillingIndicator.ITBIS_2: "reduced",
    BillingIndicator.ITBIS_3: "zero",
}


_SERVICE: Final = Priced(unit_price=D("5000.00"), description="SERVICIO DE PLOMERIA", goods=False)
_PARTS: Final = Priced(unit_price=D("85.00"), quantity=D(4), category=EXEMPT, description="TUBO PVC")
_CONSULTANCY: Final = Priced(unit_price=D("1000.00"), category=EXEMPT, description="CONSULTORIA")


def _purchase(withholdings: FieldValue, **fields: str) -> FiscalDraft:
    return fiscal_draft(
        _SERVICE,
        _PARTS,
        direction=PURCHASE,
        fields={"ecf_type": "41", "withholdings": withholdings, **fields},
    )


def _abroad(withholdings: FieldValue) -> FiscalDraft:
    return fiscal_draft(
        _CONSULTANCY,
        direction=PURCHASE,
        counterparty=PAYEE,
        fields={"ecf_type": "47", "withholdings": withholdings},
    )


def test_a_purchase_s_withholdings_reach_its_e_cf_lines() -> None:
    draft = _purchase({"0": {"itbis": "900.00", "isr": "500.00"}}, withholding_role="2")
    assert found(draft) == []
    drafted = ecf_draft(
        draft, number=Encf(EcfType.PURCHASES, 1), accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON
    )
    service, parts = drafted.lines
    assert (service.itbis_withheld, service.isr_withheld) == (D("900.00"), D("500.00"))
    assert (parts.itbis_withheld, parts.isr_withheld) == (None, None)
    assert drafted.withholding_role is WithholdingRole.PERCEPTION
    abroad = ecf_draft(
        _abroad({"0": {"isr": "270.00"}}),
        number=Encf(EcfType.FOREIGN_PAYMENTS, 1),
        accepted=NONE_ACCEPTED,
        expires_on=EXPIRES_ON,
    )
    assert abroad.lines[0].isr_withheld == D("270.00")
    assert abroad.withholding_role is WithholdingRole.RETENTION


@pytest.mark.parametrize(
    ("withholdings", "expected"),
    [
        ({"2": {"isr": "1.00"}}, [("do_ecf_field_invalid", "fiscal.do.withholdings.2")]),
        ({"0": {"itbis": "900.01"}}, [("do_ecf_withheld_too_large", "fiscal.do.withholdings.0.itbis")]),
        ({"1": {"itbis": "0.01"}}, [("do_ecf_withheld_too_large", "fiscal.do.withholdings.1.itbis")]),
        ({"0": {"isr": "5000.01"}}, [("do_ecf_withheld_too_large", "fiscal.do.withholdings.0.isr")]),
        ({"0": {"isr": "1.001"}}, [("do_ecf_withheld_precision", "fiscal.do.withholdings.0.isr")]),
        (
            {"0": {"itbis": "10000000000000000"}},
            [("do_ecf_amount_too_large", "fiscal.do.withholdings.0.itbis")],
        ),
        (
            {"1": {"isr": "340.01"}, "0": {"itbis": "1000"}},
            [
                ("do_ecf_withheld_too_large", "fiscal.do.withholdings.0.itbis"),
                ("do_ecf_withheld_too_large", "fiscal.do.withholdings.1.isr"),
            ],
        ),
    ],
)
def test_a_withholding_is_at_most_the_line_s_itbis_or_amount(
    withholdings: FieldValue, expected: list[tuple[str, str]]
) -> None:
    assert found(_purchase(withholdings)) == expected


def test_a_payment_abroad_withholds_isr_on_every_line_and_no_itbis() -> None:
    assert found(_abroad({"0": {"isr": "270.00"}})) == []
    assert found(_abroad({})) == [("do_ecf_value_required", "fiscal.do.withholdings.0.isr")]
    assert found(_abroad({"0": {"itbis": "1.00", "isr": "270.00"}})) == [
        ("do_ecf_field_invalid", "fiscal.do.withholdings.0.itbis")
    ]


def test_a_payment_abroad_is_withheld_never_perceived() -> None:
    perceived = fiscal_draft(
        _CONSULTANCY,
        direction=PURCHASE,
        counterparty=PAYEE,
        fields={"ecf_type": "47", "withholdings": {"0": {"isr": "270.00"}}, "withholding_role": "2"},
    )
    assert found(perceived) == [("do_ecf_field_invalid", "fiscal.do.withholding_role")]


def test_isr_is_held_to_the_line_s_rounded_amount_by_the_objection_and_the_e_cf_alike() -> None:
    odd = Priced(unit_price=D("10.005"), category=EXEMPT, description="CONSULTORIA")
    draft = fiscal_draft(
        odd,
        direction=PURCHASE,
        counterparty=PAYEE,
        fields={"ecf_type": "47", "withholdings": {"0": {"isr": "10.01"}}},
    )
    assert found(draft) == []
    drafted = ecf_draft(
        draft, number=Encf(EcfType.FOREIGN_PAYMENTS, 1), accepted=NONE_ACCEPTED, expires_on=EXPIRES_ON
    )
    assert drafted.lines[0].isr_withheld == D("10.01")


def test_a_foreign_line_whose_pesos_drift_beyond_dgii_s_tolerance_is_objected() -> None:
    screws = Priced(unit_price=D("0.0001"), quantity=D(100000), category=EXEMPT, description="TORNILLO")
    draft = fiscal_draft(screws, currency=USD, exchange_rate=D("58.7512"), counterparty=None)
    assert found(draft) == [("do_ecf_conversion_inexact", "lines.0.unit_price")]
    fine = Priced(unit_price=D("10.0050"), category=EXEMPT, description="TORNILLO")
    assert found(fiscal_draft(fine, currency=USD, exchange_rate=D("58.7512"), counterparty=None)) == []
    imprecise = Priced(unit_price=D("0.0001"), quantity=D("100000.001"), category=EXEMPT)
    assert found(fiscal_draft(imprecise, currency=USD, exchange_rate=D("58.7512"), counterparty=None)) == [
        ("do_ecf_quantity_precision", "lines.0.quantity")
    ]  # a line objected to already is not converted


@pytest.mark.parametrize("ecf_type", ["31", "43"])
def test_withholdings_are_given_on_a_purchase_or_a_payment_abroad_only(ecf_type: str) -> None:
    direction = PURCHASE if ecf_type == "43" else SALES
    draft = fiscal_draft(
        Priced(unit_price=D(100), category=EXEMPT),
        direction=direction,
        counterparty=None if ecf_type == "43" else BUYER,
        fields={"ecf_type": ecf_type, "withholdings": {"0": {"isr": "1.00"}}, "withholding_role": "1"},
    )
    assert found(draft) == [
        ("do_ecf_field_invalid", "fiscal.do.withholding_role"),
        ("do_ecf_field_invalid", "fiscal.do.withholdings"),
    ]


def test_a_foreign_unit_price_beyond_sixteen_digits_in_pesos_is_objected() -> None:
    huge = Priced(unit_price=D("9000000000000000.00"), quantity=D("0.01"), category=EXEMPT)
    draft = fiscal_draft(huge, currency=USD, exchange_rate=D(2), counterparty=None)
    assert ("do_ecf_amount_too_large", "lines.0.unit_price") in found(draft)
