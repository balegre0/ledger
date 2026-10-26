from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Final
lazy from collections.abc import Callable
lazy from contextlib import AbstractContextManager

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    BUYER_PARTS,
    EXEMPT_ONLY_TYPES,
    ISR_REQUIRED_TYPES,
    MAX_LINES,
    MAX_NOTE_LINES,
    NOTE_TYPES,
    UNDATED_TYPES,
    WITHHOLDING_TYPES,
    BillingIndicator,
    Buyer,
    BuyerPart,
    DraftInvalidError,
    EcfReference,
    Issuer,
    ModificationCode,
    Rule,
    Violation,
    WithholdingRole,
    line_violations,
    withheld_violation,
)
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType, Encf
from ledger_country_do_testing.samples import (
    BUYER,
    CREDIT_LINES,
    ISSUED_ON,
    ISSUER,
    SEQUENCE_EXPIRES_ON,
    consumer_invoice,
    credit_invoice,
)
from ledger_kernel.money import Currency
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft

LINE: Final = CREDIT_LINES[0]
REFERENCE: Final = EcfReference(
    encf=Encf(EcfType.CREDIT_INVOICE, 7), issued_on=date(2026, 10, 1), code=ModificationCode.CORRECT_AMOUNTS
)
USD: Final = Currency.of("USD")
D540: Final = Decimal("540.00")


def refused(field: str) -> AbstractContextManager[pytest.ExceptionInfo[DraftInvalidError]]:
    return pytest.raises(DraftInvalidError, match=f"^{field} ")


def drafted(ecf_type: EcfType) -> EcfDraft:
    """The sample credit invoice as an e-CF of ``ecf_type``, with what that type needs."""
    billing = (
        BillingIndicator.EXEMPT
        if ecf_type in EXEMPT_ONLY_TYPES
        else BillingIndicator.ITBIS_3
        if ecf_type is EcfType.EXPORTS
        else None
    )
    parts = BUYER_PARTS[ecf_type]
    buyer = Buyer(
        rnc=BUYER.rnc if BuyerPart.RNC in parts else None,
        name=BUYER.name if BuyerPart.NAME in parts else None,
    )
    withheld = Decimal(0) if ecf_type in ISR_REQUIRED_TYPES else None
    lines = (
        CREDIT_LINES if billing is None else tuple(replace(line, billing=billing) for line in CREDIT_LINES)
    )
    return replace(
        credit_invoice(),
        encf=Encf(ecf_type, 1),
        sequence_expires_on=None if ecf_type in UNDATED_TYPES else SEQUENCE_EXPIRES_ON,
        references=(REFERENCE,) if ecf_type in NOTE_TYPES else (),
        lines=tuple(replace(line, isr_withheld=withheld) for line in lines),
        buyer=buyer,
    )


def test_the_samples_are_drafts_of_types_31_and_32() -> None:
    assert credit_invoice().ecf_type is EcfType.CREDIT_INVOICE
    assert consumer_invoice().ecf_type is EcfType.CONSUMER_INVOICE
    assert consumer_invoice().buyer.anonymous
    assert not BUYER.anonymous


@pytest.mark.parametrize("ecf_type", list(EcfType))
def test_every_type_drafts(ecf_type: EcfType) -> None:
    assert drafted(ecf_type).ecf_type is ecf_type


@pytest.mark.parametrize(
    ("ecf_type", "most"), [(EcfType.CREDIT_INVOICE, MAX_LINES), (EcfType.CREDIT_NOTE, MAX_NOTE_LINES)]
)
def test_a_draft_holds_one_line_up_to_its_type_s_limit(ecf_type: EcfType, most: int) -> None:
    assert len(replace(drafted(ecf_type), lines=(LINE,) * most).lines) == most
    for count in (0, most + 1):
        with refused("lines"):
            replace(drafted(ecf_type), lines=(LINE,) * count)


def test_a_note_corrects_exactly_one_e_cf() -> None:
    assert drafted(EcfType.DEBIT_NOTE).references == (REFERENCE,)
    for references in ((), (REFERENCE, REFERENCE)):
        with refused("references"):
            replace(drafted(EcfType.DEBIT_NOTE), references=references)
    with refused("references"):
        replace(drafted(EcfType.CREDIT_INVOICE), references=(REFERENCE,))


@pytest.mark.parametrize("ecf_type", sorted(EXEMPT_ONLY_TYPES))
def test_types_without_taxed_totals_take_exempt_lines_only(ecf_type: EcfType) -> None:
    with refused("line.billing"):
        replace(drafted(ecf_type), lines=CREDIT_LINES)


def test_an_export_takes_zero_rate_lines_only() -> None:
    zero = replace(LINE, billing=BillingIndicator.ITBIS_3)
    assert replace(drafted(EcfType.EXPORTS), lines=(zero,)).lines == (zero,)
    for billing in (BillingIndicator.ITBIS_1, BillingIndicator.EXEMPT):
        with refused("line.billing"):
            replace(drafted(EcfType.EXPORTS), lines=(replace(LINE, billing=billing),))


def test_a_buyer_holds_only_what_its_type_s_comprador_holds() -> None:
    with refused("buyer"):
        replace(drafted(EcfType.MINOR_EXPENSES), buyer=Buyer(name="FERRETERIA"))
    for buyer in (Buyer(rnc="131098193", name="X"), Buyer(name="X", address="CALLE 5")):
        with refused("buyer"):
            replace(drafted(EcfType.FOREIGN_PAYMENTS), buyer=buyer)
    with refused("buyer"):
        replace(drafted(EcfType.CREDIT_INVOICE), buyer=replace(BUYER, foreign_id="P1234"))
    payee = Buyer(foreign_id="P1234", name="ACME INC")
    assert replace(drafted(EcfType.FOREIGN_PAYMENTS), buyer=payee).buyer == payee


def test_a_payment_due_date_is_not_given_on_minor_expenses() -> None:
    due = date(2026, 11, 12)
    assert replace(drafted(EcfType.PURCHASES), payment_due=due).payment_due == due
    with refused("payment_due"):
        replace(drafted(EcfType.MINOR_EXPENSES), payment_due=due)


def test_minor_expenses_and_foreign_payments_take_no_discount() -> None:
    discounted = replace(LINE, billing=BillingIndicator.EXEMPT, discount=Decimal("1.00"))
    assert replace(drafted(EcfType.SPECIAL_REGIMES), lines=(discounted,)).lines == (discounted,)
    for ecf_type in (EcfType.MINOR_EXPENSES, EcfType.FOREIGN_PAYMENTS):
        with refused("line.discount"):
            replace(drafted(ecf_type), lines=(discounted,))


def test_a_foreign_currency_is_one_dgii_lists_and_comes_with_its_exchange_rate() -> None:
    draft = replace(drafted(EcfType.CREDIT_INVOICE), currency=USD, exchange_rate=Decimal("58.7512"))
    assert (draft.currency, draft.exchange_rate) == (USD, Decimal("58.7512"))
    with refused("exchange_rate"):
        replace(drafted(EcfType.CREDIT_INVOICE), currency=USD)
    with refused("exchange_rate"):
        replace(drafted(EcfType.CREDIT_INVOICE), exchange_rate=Decimal(1))
    for rate in (Decimal(1000), Decimal("58.75121"), Decimal(0)):
        with refused("exchange_rate"):
            replace(drafted(EcfType.CREDIT_INVOICE), currency=USD, exchange_rate=rate)
    with refused("currency"):
        replace(drafted(EcfType.CREDIT_INVOICE), currency=Currency.of("ARS"), exchange_rate=Decimal(1))
    special = replace(drafted(EcfType.SPECIAL_REGIMES), currency=USD, exchange_rate=Decimal(58))
    assert special.currency == USD


def test_the_sequence_s_expiry_is_given_exactly_for_dated_types() -> None:
    with refused("sequence_expires_on"):
        replace(credit_invoice(), sequence_expires_on=None)
    with refused("sequence_expires_on"):
        replace(consumer_invoice(), sequence_expires_on=SEQUENCE_EXPIRES_ON)


@pytest.mark.parametrize("ecf_type", [EcfType.CREDIT_INVOICE, EcfType.PURCHASES, EcfType.GOVERNMENT])
def test_a_type_that_requires_it_names_its_buyer_s_rnc(ecf_type: EcfType) -> None:
    with refused("buyer.rnc"):
        replace(drafted(ecf_type), buyer=Buyer(name="SOMEONE"))


@pytest.mark.parametrize(
    "ecf_type",
    [EcfType.CREDIT_INVOICE, EcfType.PURCHASES, EcfType.SPECIAL_REGIMES, EcfType.GOVERNMENT, EcfType.EXPORTS],
)
def test_a_type_that_requires_it_names_its_buyer(ecf_type: EcfType) -> None:
    with refused("buyer.name"):
        replace(drafted(ecf_type), buyer=Buyer(rnc="131098193"))


def test_a_buyer_may_be_named_by_a_foreign_identifier_and_an_address() -> None:
    buyer = Buyer(foreign_id="P12345678", name="ACME INC", address="1 MAIN ST, MIAMI")
    assert replace(drafted(EcfType.EXPORTS), buyer=buyer).buyer == buyer
    assert not buyer.anonymous


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("issuer.rnc", lambda: replace(ISSUER, rnc="1-01-85004-3")),
        ("issuer.rnc", lambda: replace(ISSUER, rnc="10185004")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name=" ")),
        ("issuer.legal_name", lambda: replace(ISSUER, legal_name="X" * 151)),
        ("issuer.address", lambda: replace(ISSUER, address="X" * 101)),
        ("buyer.rnc", lambda: Buyer(rnc="ABC")),
        ("buyer.name", lambda: Buyer(name="X" * 151)),
        ("buyer.address", lambda: Buyer(address="X" * 101)),
        ("buyer.foreign_id", lambda: Buyer(foreign_id="X" * 21)),
        ("line.name", lambda: replace(LINE, name="X" * 81)),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal(0))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("1.005"))),
        ("line.quantity", lambda: replace(LINE, quantity=Decimal("NaN"))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal(-1))),
        ("line.unit_price", lambda: replace(LINE, unit_price=Decimal("1.00001"))),
        ("line.discount", lambda: replace(LINE, discount=Decimal("0.001"))),
        ("line.discount", lambda: replace(LINE, discount=Decimal(-1))),
        ("line.discount", lambda: replace(LINE, discount=Decimal("3000.01"))),
        ("line.billing", lambda: replace(LINE, billing=BillingIndicator.NOT_BILLABLE)),
        (
            "line.quantity",
            lambda: replace(LINE, quantity=Decimal(10000000000000000), unit_price=Decimal(0)),
        ),
        ("line.unit_price", lambda: replace(LINE, quantity=Decimal("0.01"), unit_price=Decimal("1E16"))),
        ("line.amount", lambda: replace(LINE, quantity=Decimal(2), unit_price=Decimal(5000000000000000))),
        (
            "line.amount",
            lambda: replace(LINE, quantity=Decimal(1), unit_price=Decimal("9999999999999999.995")),
        ),
        (
            "line.amount",
            lambda: replace(LINE, quantity=Decimal("9999999999999999.99"), unit_price=Decimal("1E15")),
        ),
    ],
)
def test_each_field_is_checked_and_named_without_its_value(field: str, build: Callable[[], object]) -> None:
    with refused(field) as caught:
        build()
    assert caught.value.field == field
    assert "10185004" not in str(caught.value)


_UNWRITABLE = ["\x00", "\x0b", "\x1f", "\t", "\n", "\r", "\x7f", "\x85", "\ud800", "￾", "￿"]
_TEXT_FIELDS: list[tuple[str, Callable[[str], object]]] = [
    ("issuer.legal_name", lambda text: replace(ISSUER, legal_name=text)),
    ("issuer.address", lambda text: replace(ISSUER, address=text)),
    ("buyer.name", lambda text: Buyer(name=text)),
    ("buyer.address", lambda text: Buyer(address=text)),
    ("buyer.foreign_id", lambda text: Buyer(foreign_id=text)),
    ("line.name", lambda text: replace(LINE, name=text)),
]


@pytest.mark.parametrize("character", _UNWRITABLE)
@pytest.mark.parametrize(("field", "build"), _TEXT_FIELDS)
def test_a_text_holds_no_control_character_and_nothing_xml_cannot_hold(
    character: str, field: str, build: Callable[[str], object]
) -> None:
    with refused(field) as caught:
        build(f"CEMENTO{character}GRIS")
    assert caught.value.field == field
    assert "CEMENTO" not in str(caught.value)


@pytest.mark.parametrize("text", ["CAFÉ AÑEJO", "TUBO 1/2”", "퟿�\U0001f527\U0010ffff"])
def test_a_text_holds_any_character_xml_can_hold(text: str) -> None:
    assert replace(LINE, name=text).name == text


def test_an_issuer_s_municipality_and_province_are_codes_of_dgii_s_catalogue() -> None:
    issuer = Issuer(
        rnc="101850043", legal_name="D", address="CALLE 1", municipality="250100", province="250000"
    )
    assert (issuer.municipality, issuer.province) == ("250100", "250000")
    with refused("issuer.municipality"):
        Issuer(rnc="101850043", legal_name="D", address="CALLE 1", municipality="999999")
    with refused("issuer.province"):
        Issuer(rnc="101850043", legal_name="D", address="CALLE 1", province="")


def test_a_cedula_is_taken_wherever_an_rnc_is() -> None:
    assert Issuer(rnc="00113918205", legal_name="JUAN PEREZ", address="CALLE 1").rnc == "00113918205"


def test_quantities_prices_and_amounts_reach_sixteen_integer_digits() -> None:
    most = Decimal("9999999999999999.99")
    assert replace(LINE, quantity=most, unit_price=Decimal(1)).quantity == most
    assert replace(LINE, quantity=Decimal(1), unit_price=Decimal("9999999999999999.9949")).quantity == 1
    assert replace(
        LINE, quantity=Decimal("0.01"), unit_price=Decimal("9999999999999999.9999")
    ).quantity == Decimal("0.01")


def test_a_discount_may_take_the_whole_gross_and_never_more() -> None:
    assert replace(LINE, discount=Decimal("3000.00")).discount == Decimal("3000.00")
    ((part, violation),) = line_violations(
        name="CEMENTO", quantity=Decimal(2), unit_price=Decimal("1500.00"), discount=Decimal("3000.01")
    )
    assert (part.value, violation.rule) == ("discount", Rule.DISCOUNT_TOO_LARGE)


def test_line_violations_are_collected_for_every_part_at_once() -> None:
    found = line_violations(
        name="X" * 81, quantity=Decimal("1.001"), unit_price=Decimal("1.00001"), discount=Decimal("0.001")
    )
    assert [(part.value, violation.rule) for part, violation in found] == [
        ("name", Rule.TEXT_TOO_LONG),
        ("quantity", Rule.QUANTITY_PRECISION),
        ("unit_price", Rule.UNIT_PRICE_PRECISION),
        ("discount", Rule.DISCOUNT_PRECISION),
    ]
    others = line_violations(
        name=" ", quantity=Decimal(0), unit_price=Decimal("1E16"), discount=Decimal("NaN")
    )
    assert [violation.rule for _, violation in others] == [
        Rule.TEXT_INVALID,
        Rule.NUMBER_INVALID,
        Rule.AMOUNT_TOO_LARGE,
        Rule.NUMBER_INVALID,
    ]
    assert all("1E16" not in violation.requirement for _, violation in others)


@pytest.mark.parametrize("days", [-1, 0])
def test_a_range_is_used_up_to_its_expiry_date(days: int) -> None:
    draft = replace(credit_invoice(), issued_on=SEQUENCE_EXPIRES_ON + timedelta(days=days))
    assert draft.sequence_expires_on == SEQUENCE_EXPIRES_ON


def test_a_range_is_not_used_after_its_expiry_date() -> None:
    with refused("sequence_expires_on") as caught:
        replace(credit_invoice(), issued_on=SEQUENCE_EXPIRES_ON + timedelta(days=1))
    assert caught.value.field == "sequence_expires_on"


def _withholding(
    ecf_type: EcfType, *, itbis_withheld: Decimal | None = None, isr_withheld: Decimal | None = None
) -> EcfDraft:
    """``drafted(ecf_type)`` whose first line withholds what is given."""
    draft = drafted(ecf_type)
    first = replace(draft.lines[0], itbis_withheld=itbis_withheld, isr_withheld=isr_withheld)
    return replace(draft, lines=(first, *draft.lines[1:]))


def test_a_purchase_withholds_itbis_and_isr_per_line_as_the_company_s_retention() -> None:
    draft = _withholding(EcfType.PURCHASES, itbis_withheld=Decimal("540.00"), isr_withheld=Decimal("300.00"))
    assert (draft.lines[0].itbis_withheld, draft.lines[0].isr_withheld) == (D540, Decimal("300.00"))
    assert draft.withholding_role is WithholdingRole.RETENTION
    perceived = replace(draft, withholding_role=WithholdingRole.PERCEPTION)
    assert perceived.withholding_role is WithholdingRole.PERCEPTION
    assert {EcfType.PURCHASES, EcfType.FOREIGN_PAYMENTS} == WITHHOLDING_TYPES


@pytest.mark.parametrize(
    "ecf_type", sorted(frozenset(EcfType) - {EcfType.PURCHASES, EcfType.FOREIGN_PAYMENTS})
)
def test_only_a_purchase_or_a_payment_abroad_withholds(ecf_type: EcfType) -> None:
    with refused("line.withheld"):
        _withholding(ecf_type, isr_withheld=Decimal("1.00"))


def test_a_payment_abroad_withholds_isr_on_every_line_and_no_itbis() -> None:
    draft = drafted(EcfType.FOREIGN_PAYMENTS)
    lines = tuple(replace(line, isr_withheld=Decimal("10.00")) for line in draft.lines)
    assert replace(draft, lines=lines).lines == lines
    with refused("line.isr_withheld"):
        replace(draft, lines=(lines[0], replace(lines[1], isr_withheld=None)))
    with refused("line.itbis_withheld"):
        replace(draft, lines=(replace(lines[0], itbis_withheld=Decimal("1.00")),))


@pytest.mark.parametrize(
    ("field", "itbis", "isr"),
    [
        ("line.itbis_withheld", Decimal("1.001"), None),
        ("line.itbis_withheld", Decimal(-1), None),
        ("line.isr_withheld", None, Decimal("NaN")),
        ("line.isr_withheld", None, Decimal("3000.01")),
    ],
)
def test_a_withheld_amount_has_two_decimals_and_never_exceeds_the_line(
    field: str, itbis: Decimal | None, isr: Decimal | None
) -> None:
    with refused(field) as caught:
        replace(LINE, itbis_withheld=itbis, isr_withheld=isr)
    assert caught.value.field == field
    assert replace(LINE, isr_withheld=Decimal("3000.00")).isr_withheld == Decimal("3000.00")


@pytest.mark.parametrize(("days", "late"), [(0, False), (30, False), (31, True)])
def test_a_credit_note_says_whether_it_comes_more_than_thirty_days_after_its_e_cf(
    *, days: int, late: bool
) -> None:
    corrected = replace(REFERENCE, issued_on=ISSUED_ON - timedelta(days=days))
    assert replace(drafted(EcfType.CREDIT_NOTE), references=(corrected,)).credit_note_late is late
    assert replace(drafted(EcfType.DEBIT_NOTE), references=(corrected,)).credit_note_late is False


def test_isr_withheld_is_held_to_the_line_s_rounded_amount() -> None:
    odd = replace(LINE, quantity=Decimal(1), unit_price=Decimal("10.005"), isr_withheld=Decimal("10.01"))
    assert odd.isr_withheld == Decimal("10.01")
    with refused("line.isr_withheld") as caught:
        replace(odd, isr_withheld=Decimal("10.02"))
    assert caught.value.field == "line.isr_withheld"


def test_a_withheld_amount_s_precision_is_its_own_rule() -> None:
    assert withheld_violation(Decimal("1.001")) == Violation(
        Rule.WITHHELD_PRECISION, "has at most 2 decimals"
    )


def test_a_payment_abroad_is_withheld_never_perceived() -> None:
    with refused("withholding_role"):
        replace(drafted(EcfType.FOREIGN_PAYMENTS), withholding_role=WithholdingRole.PERCEPTION)
