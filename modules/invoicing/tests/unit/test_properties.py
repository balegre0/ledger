"""Properties of pricing that hold for any lines a client may send, in every way a regime may round."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.pricing import EstablishmentRef, Terms, price
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import RoundingPoint, TaxCalculator, TaxCategoryKey, TaxLine
lazy from ledger_invoicing.domain.pricing import Pricing
lazy from ledger_kernel.regimes import RegimeDef

DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
CENT: Final = Decimal("0.01")
MAIN: Final = EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo")
ITBIS: Final = ("do.itbis.standard", "do.itbis.reduced", "do.itbis.exempt")

quantities = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(10_000), places=6)
prices = st.decimals(min_value=Decimal(0), max_value=Decimal(1_000_000), places=4)
rates = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal(1_000), places=6)


@st.composite
def lines(draw: st.DrawFn) -> LineTerms:
    """A line a client may send: a discount up to its gross, one ITBIS category, sometimes ISC beside it."""
    quantity, unit_price = draw(quantities), draw(prices)
    discount = draw(st.decimals(min_value=Decimal(0), max_value=quantity * unit_price, places=2))
    taxes = (draw(st.sampled_from(ITBIS)), *draw(st.sampled_from(((), ("do.isc.standard",)))))
    return LineTerms(
        description="Item", quantity=quantity, unit_price=unit_price, discount=discount, taxes=taxes
    )


documents = st.lists(lines(), min_size=1, max_size=12)
rounding = st.integers(min_value=0, max_value=2)


def priced(regime: RegimeDef, document: list[LineTerms], *, rate: Decimal | None = None) -> Pricing:
    return price(
        Terms(currency=DOP if rate is None else USD, exchange_rate=rate, lines=tuple(document)),
        regime=regime,
        functional_currency=DOP,
        establishment=MAIN,
        today=date(2026, 10, 12),
    )


@given(document=documents, variant=rounding)
def test_a_base_is_the_line_s_net_rounded_half_up_to_cents(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    for line, terms_of in zip(priced(rounding_variants[variant], document).lines, document, strict=True):
        exact = terms_of.quantity * terms_of.unit_price - terms_of.discount
        assert line.base == Money(exact.quantize(CENT, rounding=ROUND_HALF_UP), DOP)
        assert line.terms == terms_of


@given(document=documents, variant=rounding)
def test_totals_are_the_sum_of_the_lines(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    regime = rounding_variants[variant]
    pricing = priced(regime, document)
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
def test_totals_are_the_kernel_calculator_s_whatever_the_order_of_the_lines(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], variant: int
) -> None:
    regime = rounding_variants[variant]
    pricing = priced(regime, document)
    tax_lines = [
        TaxLine(base=line.base, categories=tuple(TaxCategoryKey(key) for key in line.terms.taxes))
        for line in pricing.lines
    ]
    assert pricing.totals == TaxCalculator(regime).totals(tax_lines, date(2026, 10, 12))
    assert priced(regime, document[::-1]).totals == pricing.totals


@given(document=documents, rate=rates)
def test_functional_totals_add_up_and_are_each_amount_converted_once(
    rounding_variants: tuple[RegimeDef, ...], document: list[LineTerms], rate: Decimal
) -> None:
    pricing = priced(rounding_variants[0], document, rate=rate)
    functional = pricing.functional
    assert functional is not None
    zero = Money.zero(DOP)
    assert functional.gross == functional.net + functional.tax
    assert functional.tax == sum((category.tax for category in functional.categories), start=zero)
    assert functional.net == Money(
        (pricing.totals.net.amount * rate).quantize(CENT, rounding=ROUND_HALF_UP), DOP
    )
    for converted, original in zip(functional.categories, pricing.totals.categories, strict=True):
        assert (converted.category, converted.percent) == (original.category, original.percent)
        assert converted.base == Money(
            (original.base.amount * rate).quantize(CENT, rounding=ROUND_HALF_UP), DOP
        )
        assert converted.tax == Money(
            (original.tax.amount * rate).quantize(CENT, rounding=ROUND_HALF_UP), DOP
        )
