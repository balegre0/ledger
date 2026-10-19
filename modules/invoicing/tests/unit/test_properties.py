"""Properties of pricing that hold for any lines a client may send, in every way a regime may round."""

from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_invoicing.domain.errors import AmountOutOfRangeError, CurrencyNotSupportedByRegimeError
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.pricing import EstablishmentRef, Terms, price
from ledger_kernel.money import CURRENCIES, Money
from ledger_kernel.regimes import RoundingPoint
lazy from ledger_invoicing.domain.pricing import Pricing
lazy from ledger_kernel.money import Currency
lazy from ledger_kernel.regimes import RegimeDef

DOP: Final = CURRENCIES["DOP"]
USD: Final = CURRENCIES["USD"]
CENT: Final = Decimal("0.01")
EXACT: Final = Context(prec=80)
"""Wide enough for a 12-place price times a 6-place quantity near 10^18: the tests' arithmetic is exact."""
MAIN: Final = EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo")
ITBIS: Final = ("do.itbis.standard", "do.itbis.reduced", "do.itbis.exempt")

quantities = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(100_000), places=6)
prices = st.decimals(min_value=Decimal(0), max_value=Decimal(10) ** 11, places=12)
huge_quantities = st.decimals(min_value=Decimal(1), max_value=Decimal(10) ** 18 - 1, places=0)
huge_prices = st.decimals(min_value=Decimal(0), max_value=Decimal(10) ** 18 - 1, places=12)
rates = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal(1_000), places=6)


def exact_net(line: LineTerms) -> Decimal:
    return EXACT.subtract(EXACT.multiply(line.quantity, line.unit_price), line.discount)


@st.composite
def lines(draw: st.DrawFn, *, huge: bool = False) -> LineTerms:
    """A line a client may send: a discount up to its gross, one ITBIS category, sometimes ISC beside it."""
    quantity = draw(huge_quantities if huge else quantities)
    unit_price = draw(huge_prices if huge else prices)
    gross = EXACT.multiply(quantity, unit_price)
    discount = draw(st.decimals(min_value=Decimal(0), max_value=min(gross, Decimal(10) ** 17), places=12))
    taxes = (draw(st.sampled_from(ITBIS)), *draw(st.sampled_from(((), ("do.isc.standard",)))))
    return LineTerms(
        description="Item", quantity=quantity, unit_price=unit_price, discount=discount, taxes=taxes
    )


documents = st.lists(lines(), min_size=1, max_size=12)
huge_documents = st.lists(lines(huge=True), min_size=1, max_size=3)
rounding = st.integers(min_value=0, max_value=2)


def priced(
    regime: RegimeDef, document: list[LineTerms], *, currency: Currency = DOP, rate: Decimal | None = None
) -> Pricing:
    return price(
        Terms(currency=currency, exchange_rate=rate, lines=tuple(document)),
        regime=regime,
        functional_currency=DOP,
        establishment=MAIN,
        today=date(2026, 10, 12),
    )


def cents(amount: Decimal) -> Money:
    return Money(amount.quantize(CENT, rounding=ROUND_HALF_UP, context=EXACT), DOP)


def assert_adds_up(regime: RegimeDef, pricing: Pricing) -> None:
    """Totals are the sum of the lines: the net theirs, each category's base theirs, its tax near theirs."""
    totals, zero = pricing.totals, Money.zero(DOP)
    assert totals.net == sum((line.base for line in pricing.lines), start=zero)
    assert totals.gross == totals.net + totals.tax
    for category in totals.categories:
        taxed = [line for line in pricing.lines if category.category in line.terms.taxes]
        assert category.base == sum((line.base for line in taxed), start=zero)
        line_taxes = sum(
            (tax.tax for line in taxed for tax in line.taxes if tax.category == category.category), start=zero
        )
        if RoundingPoint.CATEGORY in regime.rounding.points:
            # Each line's tax is rounded on its own, the category's once: they part by half a cent a line.
            assert abs(line_taxes.amount - category.tax.amount) <= CENT / 2 * (len(taxed) + 1)
        else:
            assert line_taxes == category.tax


@given(document=documents, variant=rounding)
def test_a_base_is_the_line_s_net_rounded_half_up_to_cents(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    for line, terms_of in zip(priced(rounding_variants[variant], document).lines, document, strict=True):
        assert line.base == cents(exact_net(terms_of))
        assert line.terms == terms_of


@given(document=documents, variant=rounding)
def test_totals_are_the_sum_of_the_lines(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    regime = rounding_variants[variant]
    assert_adds_up(regime, priced(regime, document))


@given(document=huge_documents, variant=rounding)
def test_amounts_near_money_s_limit_add_up_or_are_refused_whole(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    regime = rounding_variants[variant]
    try:
        pricing = priced(regime, document)
    except AmountOutOfRangeError:
        return
    assert_adds_up(regime, pricing)


@given(document=documents, variant=rounding)
def test_totals_do_not_depend_on_the_order_of_the_lines(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    regime = rounding_variants[variant]
    assert priced(regime, document[::-1]).totals == priced(regime, document).totals


@given(document=documents, rate=rates)
def test_functional_totals_add_up_and_are_each_amount_converted_once(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], rate: Decimal
) -> None:
    pricing = priced(rounding_variants[0], document, currency=USD, rate=rate)
    functional = pricing.functional
    assert functional is not None
    zero = Money.zero(DOP)
    assert functional.gross == functional.net + functional.tax
    assert functional.tax == sum((category.tax for category in functional.categories), start=zero)
    assert functional.net == cents(EXACT.multiply(pricing.totals.net.amount, rate))
    for converted, original in zip(functional.categories, pricing.totals.categories, strict=True):
        assert (converted.category, converted.percent) == (original.category, original.percent)
        assert converted.base == cents(EXACT.multiply(original.base.amount, rate))
        assert converted.tax == cents(EXACT.multiply(original.tax.amount, rate))


@given(document=documents, currency=st.sampled_from(sorted(CURRENCIES)), rate=rates)
def test_any_currency_is_priced_when_its_minor_unit_is_the_regime_s_places_and_refused_otherwise(
    regime: RegimeDef, document: list[LineTerms], currency: str, rate: Decimal
) -> None:
    sold_in = CURRENCIES[currency]
    rate_if_foreign = None if sold_in == DOP else rate
    if sold_in.minor_units != regime.rounding.places:
        with pytest.raises(CurrencyNotSupportedByRegimeError):
            priced(regime, document, currency=sold_in, rate=rate_if_foreign)
        return
    pricing = priced(regime, document, currency=sold_in, rate=rate_if_foreign)
    assert pricing.totals.gross.currency == sold_in
    assert (pricing.functional is None) == (sold_in == DOP)
