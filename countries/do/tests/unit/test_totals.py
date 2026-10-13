from dataclasses import replace
from datetime import date
from decimal import ROUND_DOWN, Decimal
lazy import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, DraftInvalidError, Line
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, RegimeNotInForceError
from ledger_country_do.channels.ecf_v1.domain.routing import route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do.regime.taxes import STANDARD
from ledger_country_do_testing.samples import consumer_invoice, credit_invoice

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


def test_a_taxed_category_without_a_rate_is_a_defect_of_the_regime() -> None:
    broken = replace(REGIME, tax_categories=(replace(STANDARD, rates=None), *REGIME.tax_categories[1:]))
    with pytest.raises(EcfError, match=r"do.itbis.standard has no rate") as caught:
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
    down = replace(REGIME, rounding=replace(ROUNDING, mode=ROUND_DOWN))
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
