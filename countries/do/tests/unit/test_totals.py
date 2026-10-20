from dataclasses import replace
from datetime import date
from decimal import Decimal
lazy import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    CATEGORY_OF,
    BillingIndicator,
    DraftInvalidError,
    Line,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, RegimeNotInForceError
from ledger_country_do.channels.ecf_v1.domain.routing import route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do.regime.taxes import STANDARD
from ledger_country_do_testing.samples import (
    consumer_invoice,
    credit_invoice,
    credit_note,
    export_sale,
    payment_abroad,
    purchase,
)
from ledger_kernel.money import Currency, Money, RoundingMode
from ledger_kernel.regimes import TaxCalculator, TaxCategoryKey, TaxLine

D = Decimal


def test_the_credit_invoice_s_totals_are_dgii_s() -> None:
    totals = compute_totals(credit_invoice())
    assert totals.line_amounts == (D("3000.00"), D("850.50"), D("361.00"))
    first, second = totals.groups
    assert (first.indicator, first.base, first.percent, first.itbis) == (
        BillingIndicator.ITBIS_1,
        D("3000.00"),
        D(18),
        D("540.00"),
    )
    assert (second.indicator, second.base, second.percent, second.itbis) == (
        BillingIndicator.ITBIS_2,
        D("850.50"),
        D(16),
        D("136.08"),
    )
    assert (totals.taxed_total, totals.exempt, totals.itbis_total, totals.total) == (
        D("3850.50"),
        D("361.00"),
        D("676.08"),
        D("4887.58"),
    )
    assert totals.group(BillingIndicator.ITBIS_3) is None


def test_a_document_without_taxed_lines_has_no_taxed_totals() -> None:
    exempt = Line(name="LIBRO", quantity=D(1), unit_price=D("100.00"), billing=BillingIndicator.EXEMPT)
    totals = compute_totals(replace(consumer_invoice(), lines=(exempt,)))
    assert (totals.groups, totals.taxed_total, totals.itbis_total, totals.total) == (
        (),
        None,
        None,
        D("100.00"),
    )


def test_the_zero_rate_is_a_taxed_group_with_no_itbis() -> None:
    zero = Line(name="EXPORTACION", quantity=D(1), unit_price=D("100.00"), billing=BillingIndicator.ITBIS_3)
    totals = compute_totals(replace(consumer_invoice(), lines=(zero,)))
    (group,) = totals.groups
    assert (group.percent, group.itbis, totals.exempt, totals.total) == (D(0), D("0.00"), None, D("100.00"))


def test_a_taxed_category_the_regime_lacks_is_a_defect_of_the_regime() -> None:
    broken = replace(
        REGIME, tax_categories=tuple(each for each in REGIME.tax_categories if each is not STANDARD)
    )
    with pytest.raises(EcfError, match=r"tax category 'do\.itbis\.standard' is not declared") as caught:
        compute_totals(consumer_invoice(), broken)
    assert (caught.value.kind, caught.value.code) == ("internal", "do_ecf_regime_defect")


def test_a_draft_dated_before_the_regime_is_a_clean_refusal() -> None:
    early = replace(credit_invoice(), issued_on=date(2018, 12, 31))
    with pytest.raises(RegimeNotInForceError, match="2018-12-31") as caught:
        compute_totals(early)
    assert (caught.value.kind, caught.value.code) == ("invalid", "do_ecf_regime_not_in_force")
    small = replace(consumer_invoice(), issued_on=date(2018, 12, 31))
    with pytest.raises(RegimeNotInForceError):
        route(small, compute_totals(consumer_invoice()))


def test_an_entry_the_regime_does_not_declare_is_a_defect_of_the_pack() -> None:
    missing = replace(REGIME, parameters={})
    with pytest.raises(EcfError) as caught:
        route(consumer_invoice(), compute_totals(consumer_invoice()), missing)
    assert (caught.value.kind, caught.value.code) == ("internal", "do_ecf_regime_defect")


def test_amounts_round_by_the_regime_s_own_rule() -> None:
    down = replace(REGIME, rounding=replace(ROUNDING, mode=RoundingMode.DOWN))
    line = Line(name="ITEM", quantity=D(1), unit_price=D("10.0099"), billing=BillingIndicator.EXEMPT)
    draft = replace(consumer_invoice(), lines=(line,))
    assert compute_totals(draft).line_amounts == (D("10.01"),)
    assert compute_totals(draft, down).line_amounts == (D("10.00"),)


_LINES = st.lists(
    st.builds(
        Line,
        name=st.just("ITEM"),
        quantity=st.decimals(min_value=D("0.01"), max_value=D(9999), places=2),
        unit_price=st.decimals(min_value=D(0), max_value=D(99999), places=4),
        billing=st.sampled_from(
            [
                BillingIndicator.ITBIS_1,
                BillingIndicator.ITBIS_2,
                BillingIndicator.ITBIS_3,
                BillingIndicator.EXEMPT,
            ]
        ),
    ),
    min_size=1,
    max_size=20,
)


@given(_LINES, st.randoms())
def test_the_total_is_the_sum_of_its_parts_whatever_the_order(
    lines: list[Line], order: random.Random
) -> None:
    draft = replace(consumer_invoice(), lines=tuple(lines))
    totals = compute_totals(draft)
    parts = sum((group.base + group.itbis for group in totals.groups), D(0)) + (totals.exempt or D(0))
    assert totals.total == parts
    shuffled = list(lines)
    order.shuffle(shuffled)
    assert compute_totals(replace(draft, lines=tuple(shuffled))).total == totals.total


_MOST = D("9999999999999999.99")


def test_a_total_reaches_sixteen_integer_digits() -> None:
    line = Line(name="ITEM", quantity=D(1), unit_price=_MOST, billing=BillingIndicator.EXEMPT)
    assert compute_totals(replace(consumer_invoice(), lines=(line,))).total == _MOST


@pytest.mark.parametrize(
    "lines",
    [
        (Line(name="ITEM", quantity=D(1), unit_price=_MOST, billing=BillingIndicator.EXEMPT),) * 2,
        (
            Line(
                name="ITEM",
                quantity=D(1),
                unit_price=D("9000000000000000.00"),
                billing=BillingIndicator.ITBIS_1,
            ),
        ),
    ],
    ids=["exempt-sum", "itbis"],
)
def test_a_total_of_seventeen_integer_digits_is_refused(lines: tuple[Line, ...]) -> None:
    with pytest.raises(DraftInvalidError, match=r"^total ") as caught:
        compute_totals(replace(consumer_invoice(), lines=lines))
    assert caught.value.field == "total"


_PESO = Currency.of("DOP")


@given(_LINES)
def test_the_kernel_s_calculator_totals_the_lines_as_dgii_does(lines: list[Line]) -> None:
    # The regime's rounding rule is the one the channel computes with: the kernel's one calculator, given each
    # line's amount, finds the channel's ITBIS and total.
    draft = replace(consumer_invoice(), lines=tuple(lines))
    totals = compute_totals(draft)
    taxed = [
        TaxLine(base=Money.of(amount, _PESO), categories=(TaxCategoryKey(CATEGORY_OF[line.billing]),))
        for line, amount in zip(draft.lines, totals.line_amounts, strict=True)
    ]
    kernel = TaxCalculator(REGIME).totals(taxed, draft.issued_on)
    assert kernel.tax.amount == (totals.itbis_total or D(0))
    assert kernel.gross.amount == totals.total
    by_category = {category.category: category for category in kernel.categories}
    for group in totals.groups:
        found = by_category.pop(TaxCategoryKey(CATEGORY_OF[group.indicator]))
        assert (found.base.amount, found.percent, found.tax.amount) == (
            group.base,
            group.percent,
            group.itbis,
        )
    exempt = by_category.pop(TaxCategoryKey(CATEGORY_OF[BillingIndicator.EXEMPT]), None)
    assert by_category == {}
    if exempt is None:
        assert totals.exempt is None
    else:
        assert (exempt.base.amount, exempt.tax.amount) == (totals.exempt, D(0))


def test_a_line_s_amount_is_quantity_times_price_less_its_discount() -> None:
    discounted = Line(
        name="ITEM",
        quantity=D(3),
        unit_price=D("100.3333"),
        discount=D("10.50"),
        billing=BillingIndicator.ITBIS_1,
    )
    totals = compute_totals(replace(consumer_invoice(), lines=(discounted,)))
    assert totals.line_amounts == (D("290.50"),)
    (group,) = totals.groups
    assert (group.base, group.itbis, totals.total) == (D("290.50"), D("52.29"), D("342.79"))


_USD = Currency.of("USD")


def test_a_foreign_currency_s_totals_are_its_own_and_pesos_at_the_exchange_rate() -> None:
    draft = replace(credit_invoice(), currency=_USD, exchange_rate=D("58.7512"))
    totals = compute_totals(draft)
    foreign = totals.foreign
    assert foreign is not None
    assert foreign.foreign is None
    assert foreign.line_amounts == (D("3000.00"), D("850.50"), D("361.00"))
    assert (foreign.itbis_total, foreign.total) == (D("676.08"), D("4887.58"))
    assert totals.line_amounts == (D("176253.60"), D("49967.90"), D("21209.18"))
    first, second = totals.groups
    assert (first.base, first.itbis) == (D("176253.60"), D("31725.65"))
    assert (second.base, second.itbis) == (D("49967.90"), D("7994.86"))
    assert totals.exempt == D("21209.18")
    assert totals.total == D("176253.60") + D("31725.65") + D("49967.90") + D("7994.86") + D("21209.18")
    assert compute_totals(credit_invoice()).foreign is None


def test_a_total_in_pesos_of_seventeen_integer_digits_is_refused() -> None:
    line = Line(
        name="ITEM", quantity=D(1), unit_price=D("9000000000000000.00"), billing=BillingIndicator.EXEMPT
    )
    draft = replace(consumer_invoice(), lines=(line,), currency=_USD, exchange_rate=D(2))
    with pytest.raises(DraftInvalidError, match=r"^total ") as caught:
        compute_totals(draft)
    assert caught.value.field == "total"


def test_each_line_s_figures_are_written_in_pesos_and_in_its_currency() -> None:
    draft = replace(credit_invoice(), currency=_USD, exchange_rate=D("58.7512"))
    discounted = replace(draft, lines=(replace(draft.lines[0], discount=D("10.50")), *draft.lines[1:]))
    totals = compute_totals(discounted)
    foreign = totals.foreign
    assert foreign is not None
    first = totals.lines[0]
    assert (first.unit_price, first.discount, first.amount) == (D("88126.8000"), D("616.89"), D("175636.71"))
    assert (foreign.lines[0].unit_price, foreign.lines[0].discount) == (D("1500.00"), D("10.50"))
    assert totals.lines[2].unit_price == D("7069.7258")  # 120.3333 x 58.7512, four places
    assert compute_totals(credit_invoice()).lines[0].unit_price == D("1500.00")


def test_a_purchase_s_withholdings_are_summed_and_converted_like_every_amount() -> None:
    totals = compute_totals(purchase(withholdings=True))
    assert (totals.lines[0].itbis_withheld, totals.lines[0].isr_withheld) == (D("900.00"), D("500.00"))
    assert (totals.lines[1].itbis_withheld, totals.lines[1].isr_withheld) == (None, None)
    assert (totals.itbis_withheld, totals.isr_withheld) == (D("900.00"), D("500.00"))
    assert (compute_totals(purchase()).itbis_withheld, compute_totals(purchase()).isr_withheld) == (
        None,
        None,
    )
    abroad = compute_totals(payment_abroad())
    assert abroad.isr_withheld == D("15862.82")  # 270.00 x 58.7512
    assert abroad.foreign is not None
    assert abroad.foreign.isr_withheld == D("270.00")


def test_itbis_withheld_is_at_most_the_line_s_itbis() -> None:
    draft = purchase(withholdings=True)
    service, parts = draft.lines
    with pytest.raises(DraftInvalidError, match=r"^line\.itbis_withheld ") as caught:
        compute_totals(replace(draft, lines=(replace(service, itbis_withheld=D("900.01")), parts)))
    assert caught.value.field == "line.itbis_withheld"
    with pytest.raises(DraftInvalidError, match=r"^line\.itbis_withheld "):
        compute_totals(replace(draft, lines=(service, replace(parts, itbis_withheld=D("0.01")))))


def test_a_unit_price_in_pesos_of_seventeen_integer_digits_is_refused() -> None:
    line = Line(
        name="ITEM", quantity=D("0.01"), unit_price=D("9000000000000000.00"), billing=BillingIndicator.EXEMPT
    )
    draft = replace(consumer_invoice(), lines=(line,), currency=_USD, exchange_rate=D(2))
    with pytest.raises(DraftInvalidError, match=r"^line\.unit_price ") as caught:
        compute_totals(draft)
    assert caught.value.field == "line.unit_price"


def test_pesos_follow_dgii_s_arithmetic_from_each_line_s_unit_price_in_pesos() -> None:
    totals = compute_totals(credit_note(foreign=True))  # 25.53 USD at 18 %, 58.7512 pesos the dollar
    (line,) = totals.lines
    assert (line.unit_price, line.amount) == (D("1499.9181"), D("1499.92"))
    (group,) = totals.groups
    assert (group.base, group.itbis, totals.total) == (D("1499.92"), D("269.99"), D("1769.91"))
    assert totals.foreign is not None
    assert (totals.foreign.total, totals.foreign.groups[0].itbis) == (D("30.13"), D("4.60"))


def test_a_line_s_amount_in_pesos_is_its_quantity_times_its_price_in_pesos() -> None:
    line = Line(name="X", quantity=D(1), unit_price=D("10.0050"), billing=BillingIndicator.ITBIS_3)
    totals = compute_totals(replace(export_sale(), lines=(line,)))
    (figures,) = totals.lines
    assert (figures.unit_price, figures.amount) == (D("587.8058"), D("587.81"))


def test_a_line_whose_pesos_drift_beyond_dgii_s_tolerance_is_refused() -> None:
    screws = Line(
        name="TORNILLO", quantity=D(100000), unit_price=D("0.0001"), billing=BillingIndicator.ITBIS_3
    )
    with pytest.raises(DraftInvalidError, match=r"^line\.unit_price ") as caught:
        compute_totals(replace(export_sale(), lines=(screws,)))
    assert caught.value.field == "line.unit_price"


def test_withheld_itbis_in_pesos_is_at_most_the_line_s_itbis_in_pesos() -> None:
    draft = purchase(withholdings=True)
    service, _ = draft.lines
    whole = replace(service, unit_price=D("25.53"), itbis_withheld=D("4.60"), isr_withheld=D("2.55"))
    part = replace(whole, itbis_withheld=D("4.59"))
    for line, expected in ((whole, D("269.99")), (part, D("269.67"))):
        totals = compute_totals(replace(draft, lines=(line,), currency=_USD, exchange_rate=D("58.7512")))
        (figures,) = totals.lines
        assert (totals.groups[0].itbis, figures.itbis_withheld) == (D("269.99"), expected)
        assert figures.isr_withheld == D("149.82")
    whole_isr = replace(whole, isr_withheld=D("25.53"))
    totals = compute_totals(replace(draft, lines=(whole_isr,), currency=_USD, exchange_rate=D("58.7512")))
    assert totals.lines[0].isr_withheld == totals.lines[0].amount


def test_a_wholly_discounted_line_in_pesos_is_zero_never_negative() -> None:
    line = Line(
        name="ITEM",
        quantity=D("3.05"),
        unit_price=D("0.3056"),
        discount=D("0.93"),
        billing=BillingIndicator.EXEMPT,
    )
    totals = compute_totals(
        replace(consumer_invoice(), lines=(line,), currency=_USD, exchange_rate=D("0.0054"))
    )
    (figures,) = totals.lines
    assert figures.amount == 0
    assert not figures.amount.is_signed()
