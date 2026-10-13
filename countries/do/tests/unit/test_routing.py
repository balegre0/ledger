from dataclasses import replace
from decimal import Decimal

import pytest

from ledger_country_do.channels.ecf_v1.domain.drafts import BillingIndicator, Buyer, DraftInvalidError, Line
from ledger_country_do.channels.ecf_v1.domain.routing import Route, route
from ledger_country_do.channels.ecf_v1.domain.totals import compute_totals
from ledger_country_do_testing.samples import BUYER, consumer_invoice, credit_invoice


def exempt_consumer_invoice(amount: str, buyer: Buyer) -> Route:
    line = Line(
        name="LIBROS", quantity=Decimal(1), unit_price=Decimal(amount), billing=BillingIndicator.EXEMPT
    )
    draft = replace(consumer_invoice(), lines=(line,), buyer=buyer)
    return route(draft, compute_totals(draft))


def test_a_consumer_invoice_below_the_threshold_goes_as_an_rfce() -> None:
    draft = consumer_invoice()
    assert route(draft, compute_totals(draft)) is Route.RFCE
    assert exempt_consumer_invoice("249999.99", Buyer()) is Route.RFCE


def test_from_the_threshold_it_goes_in_full() -> None:
    draft = consumer_invoice(large=True)
    assert route(draft, compute_totals(draft)) is Route.RECEPTION
    assert exempt_consumer_invoice("250000.00", BUYER) is Route.RECEPTION


def test_a_consumer_invoice_from_the_threshold_names_its_buyer() -> None:
    with pytest.raises(DraftInvalidError, match=r"^buyer "):
        exempt_consumer_invoice("250000.00", Buyer(name="SOMEONE"))


def test_every_other_type_goes_in_full() -> None:
    draft = credit_invoice()
    assert route(draft, compute_totals(draft)) is Route.RECEPTION
