from decimal import Decimal

from ledger_country_do.channels.ecf_v1.domain.drafts import Buyer
from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do_testing.samples import (
    BUYER,
    ISSUER,
    USD,
    consumer_invoice,
    credit_invoice,
    credit_note,
    debit_note,
    export_sale,
    government_sale,
    minor_expense,
    payment_abroad,
    purchase,
    special_regime_sale,
)


def test_samples_take_what_a_test_changes() -> None:
    other = Buyer(rnc="131880738", name="OTRA EMPRESA SRL")
    assert credit_invoice(7, buyer=other).buyer == other
    assert str(credit_invoice(7).encf) == "E310000000007"
    assert consumer_invoice(large=True).buyer == BUYER
    assert consumer_invoice(buyer=other, issuer=ISSUER).buyer == other


def test_every_e_cf_type_has_a_sample() -> None:
    samples = (
        credit_invoice(),
        consumer_invoice(),
        debit_note(),
        credit_note(),
        purchase(),
        minor_expense(),
        special_regime_sale(),
        government_sale(),
        export_sale(),
        payment_abroad(),
    )
    assert {sample.ecf_type for sample in samples} == set(EcfType)


def test_the_variants_show_discounts_another_currency_and_withholdings() -> None:
    assert debit_note(discounts=True).lines[0].discount == Decimal("150.00")
    assert debit_note().lines[0].discount == 0
    assert credit_note(foreign=True).currency == USD
    assert credit_note().credit_note_late
    assert not credit_note(foreign=True).credit_note_late
    assert purchase(withholdings=True).lines[0].itbis_withheld == Decimal("900.00")
    assert purchase().lines[0].itbis_withheld is None
