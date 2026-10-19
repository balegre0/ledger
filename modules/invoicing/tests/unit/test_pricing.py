from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final

import pytest

from ledger_invoicing.domain.errors import (
    AmountOutOfRangeError,
    CurrencyNotSupportedByRegimeError,
    ExchangeRateRequiredError,
    ExchangeRateUnusedError,
    FieldInvalidError,
    FieldRequiredError,
    FieldUnknownError,
    InvalidExchangeRateError,
    InvalidIssueDateError,
    InvalidLineError,
    InvalidLinesError,
    InvalidTaxPointError,
    InvalidTextError,
    TaxRateNotInEffectError,
)
from ledger_invoicing.domain.lines import LineTerms
from ledger_invoicing.domain.pricing import (
    MAX_LINES,
    MAX_TAX_POINT_AGE,
    EstablishmentRef,
    Terms,
    local_date,
    price,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import TaxCategoryKind
lazy from ledger_invoicing.domain.pricing import FieldValue, Pricing
lazy from ledger_kernel.regimes import RegimeDef

D = Decimal
DOP: Final = Currency.of("DOP")
USD: Final = Currency.of("USD")
TODAY: Final = date(2026, 10, 12)
MAIN: Final = EstablishmentRef(code="MAIN", time_zone="America/Santo_Domingo")
QUANTITY: Final = "a finite number above 0 and below 10^18, with at most 6 decimal places"
AMOUNT: Final = "a finite number from 0 and below 10^18, with at most 12 decimal places"


def pesos(amount: str) -> Money:
    return Money.of(amount, DOP)


def a_line(quantity: str = "1", unit_price: str = "100", *taxes: str, discount: str = "0") -> LineTerms:
    return LineTerms(
        description="Widget",
        quantity=D(quantity),
        unit_price=D(unit_price),
        discount=D(discount),
        taxes=taxes or ("do.itbis.standard",),
    )


def in_pesos(*lines: LineTerms) -> Terms:
    return Terms(currency=DOP, lines=lines or (a_line(),))


def priced(regime: RegimeDef, terms: Terms) -> Pricing:
    return price(terms, regime=regime, functional_currency=DOP, establishment=MAIN, today=TODAY)


# ── Lines as a client writes them ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "field", "requirement"),
    [
        (a_line("0"), "quantity", QUANTITY),
        (a_line("-1"), "quantity", QUANTITY),
        (a_line("0.0000001"), "quantity", QUANTITY),
        (a_line("NaN"), "quantity", QUANTITY),
        (a_line("1E18"), "quantity", QUANTITY),
        (a_line("1", "-1"), "unit_price", AMOUNT),
        (a_line("1", "0.0000000000001"), "unit_price", AMOUNT),
        (a_line("1", "Infinity"), "unit_price", AMOUNT),
        (a_line(discount="-0.01"), "discount", AMOUNT),
        (a_line(discount="1E+18"), "discount", AMOUNT),
        (replace(a_line(), taxes=()), "taxes", "one or more tax categories"),
    ],
)
def test_a_line_is_refused_by_its_position_and_field(line: LineTerms, field: str, requirement: str) -> None:
    with pytest.raises(InvalidLineError) as caught:
        in_pesos(a_line(), a_line(), a_line(), line)
    assert str(caught.value) == f"lines.3.{field} must be {requirement}"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_line")


def test_a_line_keeps_what_the_client_wrote_its_description_cleaned() -> None:
    sent = LineTerms(
        description=" Café ",
        quantity=D("2.500000"),
        unit_price=D("10.123456789012"),
        discount=D("1.5"),
        taxes=("do.itbis.standard", "do.isc.standard"),
    )
    (line,) = in_pesos(sent).lines
    assert line == replace(sent, description="Café")
    assert LineTerms(description="x", quantity=D(1), unit_price=D(1), taxes=("x",)).discount == D(0)


def test_a_line_s_description_is_named_by_its_position() -> None:
    with pytest.raises(InvalidTextError, match=r"^lines\.1\.description is empty$"):
        in_pesos(a_line(), replace(a_line(), description=" "))


# ── Terms ───────────────────────────────────────────────────────────────────────


def test_a_document_has_one_line_at_least_and_a_bounded_number_of_them() -> None:
    for lines in ((), (a_line(),) * (MAX_LINES + 1)):
        with pytest.raises(InvalidLinesError, match=r"^a document has 1 to 1000 lines$") as caught:
            Terms(currency=DOP, lines=lines)
        assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_lines")
    assert len(in_pesos(*(a_line(),) * MAX_LINES).lines) == MAX_LINES


@pytest.mark.parametrize("rate", [D(0), D(-1), D("0.0000000000001"), D("NaN"), D("1E18")])
def test_an_exchange_rate_is_a_positive_number_with_at_most_12_places(rate: Decimal) -> None:
    with pytest.raises(InvalidExchangeRateError) as caught:
        Terms(currency=USD, exchange_rate=rate, lines=(a_line(),))
    assert str(caught.value) == (
        "exchange_rate must be a finite number above 0 and below 10^18, with at most 12 decimal places"
    )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_exchange_rate")


def read_only(value: object) -> bool:
    return type(value) is MappingProxyType


def test_terms_keep_a_read_only_copy_of_the_country_fields() -> None:
    fields: dict[str, dict[str, FieldValue]] = {"do": {"replaces": {"ncf": "B01"}, "note": "x"}}
    kept = Terms(currency=DOP, lines=(a_line(),), fiscal=fields)
    fields["do"]["late"] = "later"
    assert kept.fiscal == {"do": {"replaces": {"ncf": "B01"}, "note": "x"}}
    assert read_only(kept.fiscal)
    assert read_only(kept.fiscal["do"])
    assert read_only(kept.fiscal["do"]["replaces"])


def test_terms_are_a_value() -> None:
    kept = in_pesos()
    assert kept == in_pesos()
    assert (
        kept.exchange_rate,
        kept.issue_date,
        kept.tax_point,
        kept.establishment,
        kept.buyer,
        kept.fiscal,
    ) == (
        None,
        None,
        None,
        None,
        None,
        {},
    )


# ── Prices, taxes and totals ───────────────────────────────────────────────────


def test_a_line_s_base_is_quantity_times_price_less_discount_rounded_half_up(regime: RegimeDef) -> None:
    pricing = priced(
        regime, in_pesos(a_line("3", "33.333"), a_line("1", "10.005"), a_line("2", "5", discount="0.004"))
    )
    first, second, third = pricing.lines
    assert (first.gross, first.discount, first.base) == (pesos("99.999"), pesos("0"), pesos("100.00"))
    assert second.base == pesos("10.01")
    assert (third.gross, third.base) == (pesos("10"), pesos("10.00"))
    assert pricing.totals.net == pesos("120.01")
    assert first.terms == a_line("3", "33.333")


def test_a_discount_above_the_line_s_gross_is_refused(regime: RegimeDef) -> None:
    with pytest.raises(
        InvalidLineError, match=r"^lines\.1\.discount must be at most the line's quantity times"
    ):
        priced(regime, in_pesos(a_line(), a_line("2", "5", discount="10.01")))
    assert priced(regime, in_pesos(a_line("2", "5", discount="10"))).lines[0].base == pesos("0.00")


def test_totals_are_the_regime_s_calculator_s(regime: RegimeDef) -> None:
    pricing = priced(
        regime,
        in_pesos(
            a_line("1", "3000", "do.itbis.standard"),
            a_line("1", "850.50", "do.itbis.reduced"),
            a_line("1", "361", "do.itbis.exempt"),
        ),
    )
    totals = pricing.totals
    assert [(each.category, each.base, each.tax) for each in totals.categories] == [
        ("do.itbis.exempt", pesos("361"), pesos("0.00")),
        ("do.itbis.reduced", pesos("850.50"), pesos("136.08")),
        ("do.itbis.standard", pesos("3000"), pesos("540.00")),
    ]
    assert (totals.net, totals.tax, totals.gross) == (pesos("4211.50"), pesos("676.08"), pesos("4887.58"))
    assert pricing.functional is None


def test_each_line_records_the_resolved_rate_it_was_taxed_at(regime: RegimeDef) -> None:
    (line,) = priced(regime, in_pesos(a_line("1", "10.05"))).lines
    (tax,) = line.taxes
    assert (tax.category, tax.kind, tax.percent, tax.since, tax.tax) == (
        "do.itbis.standard",
        TaxCategoryKind.STANDARD,
        D(18),
        date(2019, 1, 1),
        pesos("1.81"),
    )


def test_two_taxes_on_one_line_are_each_charged_on_its_base_never_on_each_other(regime: RegimeDef) -> None:
    # A selective tax that ITBIS is charged on top of (ISC) cannot be expressed: the kernel's tax line does
    # not compound. 18 % and 10 % of 100 are 28, not 18 % of 110 plus 10.
    pricing = priced(regime, in_pesos(a_line("1", "100", "do.itbis.standard", "do.isc.standard")))
    assert [(tax.category, tax.tax) for tax in pricing.lines[0].taxes] == [
        ("do.itbis.standard", pesos("18.00")),
        ("do.isc.standard", pesos("10.00")),
    ]
    assert (pricing.totals.net, pricing.totals.tax, pricing.totals.gross) == (
        pesos("100"),
        pesos("28.00"),
        pesos("128.00"),
    )


def test_a_line_taxed_twice_by_one_tax_is_refused(regime: RegimeDef) -> None:
    for taxes in (("do.itbis.standard", "do.itbis.exempt"), ("do.itbis.standard", "do.itbis.standard")):
        with pytest.raises(InvalidLineError, match=r"^lines\.0\.taxes must be one category of each tax$"):
            priced(regime, in_pesos(a_line("1", "1", *taxes)))


def test_a_category_the_regime_does_not_declare_is_named_by_position_only(regime: RegimeDef) -> None:
    with pytest.raises(InvalidLineError) as caught:
        priced(regime, in_pesos(a_line(), a_line("1", "1", "do.itbis.standard", "xx.secret.value")))
    assert str(caught.value) == "lines.1.taxes.1 must be a tax category of the company's regime"


def test_a_rate_is_chosen_by_the_tax_point_never_by_today(regime: RegimeDef) -> None:
    old = in_pesos(a_line("1", "100", "do.itbis.old"))
    with pytest.raises(TaxRateNotInEffectError) as caught:
        priced(regime, old)
    assert str(caught.value) == "lines.0.taxes.0 has no rate in effect on the tax point"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "tax_rate_not_in_effect")
    pricing = priced(regime, replace(old, issue_date=date(2019, 6, 2), tax_point=date(2019, 6, 1)))
    assert pricing.totals.tax == pesos("12.00")
    assert (pricing.issue_date, pricing.tax_point, pricing.lines[0].taxes[0].since) == (
        date(2019, 6, 2),
        date(2019, 6, 1),
        date(2019, 1, 1),
    )
    with pytest.raises(TaxRateNotInEffectError):
        priced(regime, replace(old, issue_date=date(2018, 12, 31)))


def test_dates_default_to_today_and_the_tax_point_to_the_issue_date(regime: RegimeDef) -> None:
    defaulted = priced(regime, in_pesos())
    assert (defaulted.issue_date, defaulted.tax_point) == (TODAY, TODAY)
    dated = priced(regime, replace(in_pesos(), issue_date=date(2026, 10, 1)))
    assert (dated.issue_date, dated.tax_point) == (date(2026, 10, 1), date(2026, 10, 1))
    assert (dated.regime, dated.establishment) == ("do", MAIN)


def test_a_document_is_never_dated_after_the_establishment_s_today(regime: RegimeDef) -> None:
    with pytest.raises(InvalidIssueDateError) as caught:
        priced(regime, replace(in_pesos(), issue_date=TODAY + timedelta(days=1)))
    assert str(caught.value) == "issue_date must not be after today in the establishment's time zone"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_issue_date")


def test_a_tax_point_is_on_or_before_the_issue_date_and_at_most_400_days_before_it(regime: RegimeDef) -> None:
    for tax_point in (TODAY + timedelta(days=1), TODAY - MAX_TAX_POINT_AGE - timedelta(days=1)):
        with pytest.raises(InvalidTaxPointError) as caught:
            priced(regime, replace(in_pesos(), tax_point=tax_point))
        assert str(caught.value) == "tax_point must be on the issue date or at most 400 days before it"
        assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "invalid_tax_point")
    oldest = TODAY - MAX_TAX_POINT_AGE
    assert priced(regime, replace(in_pesos(), tax_point=oldest)).tax_point == oldest
    assert timedelta(days=400) == MAX_TAX_POINT_AGE


def test_today_is_the_establishment_s_date() -> None:
    late_evening = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)
    assert local_date(late_evening, "America/Santo_Domingo") == date(2026, 10, 12)
    assert local_date(late_evening, "UTC") == date(2026, 10, 13)
    with pytest.raises(InvalidValueError, match=r"^instant must be an aware datetime$"):
        local_date(late_evening.replace(tzinfo=None), "UTC")


def test_a_currency_the_regime_does_not_round_to_its_minor_unit_is_refused(regime: RegimeDef) -> None:
    for code in ("JPY", "KWD"):
        terms = Terms(currency=Currency.of(code), exchange_rate=Decimal("0.4"), lines=(a_line(),))
        with pytest.raises(CurrencyNotSupportedByRegimeError) as caught:
            priced(regime, terms)
        assert str(caught.value) == (
            f"{code} has other minor units than the 2 places the company's regime rounds to"
        )
        assert (caught.value.kind, caught.value.code) == (
            ErrorKind.INVALID,
            "currency_not_supported_by_regime",
        )


def test_an_amount_out_of_money_s_range_is_refused_without_repeating_it(regime: RegimeDef) -> None:
    with pytest.raises(AmountOutOfRangeError) as caught:
        priced(regime, in_pesos(a_line("100000000000000000", "123456789")))
    assert str(caught.value) == "an amount of the document is not below 10^18"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "amount_out_of_range")
    huge = a_line("999999999999999999", "0.9")
    with pytest.raises(AmountOutOfRangeError):
        priced(regime, in_pesos(huge, huge))


# ── Currencies ──────────────────────────────────────────────────────────────────


def test_a_document_in_another_currency_needs_an_exchange_rate(regime: RegimeDef) -> None:
    with pytest.raises(ExchangeRateRequiredError) as caught:
        priced(regime, Terms(currency=USD, lines=(a_line(),)))
    assert str(caught.value) == (
        "a document in another currency than the company's functional one needs exchange_rate"
    )
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "exchange_rate_required")
    with pytest.raises(ExchangeRateUnusedError) as unused:
        priced(regime, replace(in_pesos(), exchange_rate=D("58.5")))
    assert (
        str(unused.value) == "exchange_rate goes with a document in another currency than the functional one"
    )
    assert (unused.value.kind, unused.value.code) == (ErrorKind.INVALID, "exchange_rate_unused")


def test_functional_totals_are_converted_once_and_rounded_as_the_regime_rounds(regime: RegimeDef) -> None:
    dollars = priced(
        regime,
        Terms(
            currency=USD,
            exchange_rate=D("58.123456"),
            lines=(a_line("1", "10.01", "do.itbis.standard"), a_line("1", "3.33", "do.itbis.exempt")),
        ),
    )
    assert dollars.totals.gross == Money.of("15.14", USD)
    functional = dollars.functional
    assert functional is not None
    assert [(each.category, each.base, each.tax) for each in functional.categories] == [
        ("do.itbis.exempt", pesos("193.55"), pesos("0.00")),
        ("do.itbis.standard", pesos("581.82"), pesos("104.62")),
    ]
    assert (functional.net, functional.tax, functional.gross) == (
        pesos("775.37"),
        pesos("104.62"),
        pesos("879.99"),
    )


# ── Country fields ──────────────────────────────────────────────────────────────


def with_fields(fields: dict[str, dict[str, FieldValue]]) -> Terms:
    return Terms(currency=DOP, lines=(a_line(),), fiscal=fields)


def test_a_regime_that_declares_no_field_refuses_every_one(regime: RegimeDef) -> None:
    with pytest.raises(FieldUnknownError) as caught:
        priced(regime, with_fields({"do": {"ecf_type": "31"}}))
    assert str(caught.value) == "fiscal.do holds a field the regime does not declare"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "field_unknown")
    with pytest.raises(FieldUnknownError, match=r"^fiscal holds a namespace other than the regime's, do$"):
        priced(regime, with_fields({"mx-secret": {}}))
    assert priced(regime, with_fields({"do": {}})).fiscal == {}


TEXT: Final = "text of 1 to 500 characters, none of them invisible"
CODE: Final = "text of 1 to 100 characters, none of them invisible"
DECIMAL: Final = "a plain decimal number below 10^18 with at most 12 decimal places"
OBJECT: Final = "an object of at most 20 fields named in lower case, each " + TEXT


def test_declared_fields_are_kept_when_each_is_of_its_kind(regime_with_fields: RegimeDef) -> None:
    fields: dict[str, FieldValue] = {
        "ecf_type": "3" * 100,
        "note": "Pedido 7 " + "x" * 491,
        "replaced_on": "2026-10-01",
        "rate": "-" + "1" * 18 + "." + "5" * 12,
        "replaces": {f"f{index}": "x" * 500 for index in range(20)},
    }
    assert priced(regime_with_fields, with_fields({"do": fields})).fiscal == fields


def test_a_required_field_must_be_sent(regime_with_fields: RegimeDef) -> None:
    with pytest.raises(FieldRequiredError) as caught:
        priced(regime_with_fields, in_pesos())
    assert str(caught.value) == "fiscal.do.ecf_type is required"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "field_required")


@pytest.mark.parametrize(
    ("name", "value", "requirement"),
    [
        ("ecf_type", {"x": "1"}, CODE),
        ("ecf_type", "x" * 101, CODE),
        ("ecf_type", "", CODE),
        ("note", {"x": "1"}, TEXT),
        ("note", "x" * 501, TEXT),
        ("note", "a\x00b", TEXT),
        ("note", "a\N{ZERO WIDTH SPACE}b", TEXT),
        ("replaced_on", "2026-13-01", "a date (YYYY-MM-DD)"),
        ("replaced_on", "20261001", "a date (YYYY-MM-DD)"),
        ("replaced_on", {"x": "1"}, "a date (YYYY-MM-DD)"),
        ("rate", "1e5", DECIMAL),
        ("rate", "1" * 19, DECIMAL),
        ("rate", "1." + "1" * 13, DECIMAL),
        ("rate", {"x": "1"}, DECIMAL),
        ("replaces", "B01", OBJECT),
        ("replaces", {"ncf": {"deeper": "x"}}, OBJECT),
        ("replaces", {"NCF": "x"}, OBJECT),
        ("replaces", {"ncf": "x" * 501}, OBJECT),
        ("replaces", {"ncf": "a\x07"}, OBJECT),
        ("replaces", {f"f{index}": "x" for index in range(21)}, OBJECT),
    ],
)
def test_a_field_of_another_kind_is_refused_by_its_name(
    regime_with_fields: RegimeDef, name: str, value: FieldValue, requirement: str
) -> None:
    with pytest.raises(FieldInvalidError) as caught:
        priced(regime_with_fields, with_fields({"do": {"ecf_type": "31", name: value}}))
    assert str(caught.value) == f"fiscal.do.{name} must be {requirement}"
    assert (caught.value.kind, caught.value.code) == (ErrorKind.INVALID, "field_invalid")
