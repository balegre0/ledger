from decimal import Decimal

from ledger_country_do_testing.fiscal import EXEMPT, PESO, Priced, fiscal_draft
from ledger_kernel.money import Currency, Money


def test_a_draft_is_priced_and_totalled_as_invoicing_does() -> None:
    draft = fiscal_draft()
    (line,) = draft.lines
    assert (line.base, line.taxes[0].amount) == (Money.of("1000.00", PESO), Money.of("180.00", PESO))
    assert (draft.totals.gross, draft.functional_totals, draft.exchange_rate) == (
        Money.of("1180.00", PESO),
        None,
        None,
    )


def test_a_draft_in_another_currency_carries_its_totals_in_pesos() -> None:
    dollar = Currency.of("USD")
    draft = fiscal_draft(
        Priced(unit_price=Decimal("10.005"), quantity=Decimal(3), discount=Decimal("0.01")),
        Priced(unit_price=Decimal(20), category=EXEMPT, goods=False),
        currency=dollar,
        exchange_rate=Decimal("58.7512"),
    )
    assert [line.base for line in draft.lines] == [Money.of("30.01", dollar), Money.of("20.00", dollar)]
    assert draft.lines[1].goods is False
    functional = draft.functional_totals
    assert functional is not None
    assert functional.net == Money.of("2938.15", PESO)
    assert functional.gross == functional.net + functional.tax
