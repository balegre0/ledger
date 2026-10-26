"""Decision 21's schedule: 1 min, 5 min, 30 min, 2 h, then every 6 h, for seven days from the start."""

from datetime import UTC, datetime, timedelta

import pytest

from ledger_country_do.channels.ecf_v1.domain.delivery import (
    DELIVERY_WINDOW,
    delivery_open,
    delivery_wait,
    next_delivery,
)
from ledger_country_do.channels.ecf_v1.domain.environments import Environment
from ledger_country_do.channels.ecf_v1.domain.mapping import buyer_rnc
from ledger_country_do_testing.fiscal import fiscal_draft
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.regimes import Direction

START = datetime(2026, 10, 13, 12, tzinfo=UTC)


def test_the_waits_grow_then_stay_at_six_hours() -> None:
    assert [delivery_wait(attempts) for attempts in range(1, 8)] == [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=6),
        timedelta(hours=6),
        timedelta(hours=6),
    ]
    with pytest.raises(InvalidValueError, match="delivery_attempts"):
        delivery_wait(0)


def test_a_delivery_is_tried_for_seven_days_and_no_wait_reaches_past_them() -> None:
    assert delivery_open(START, START + DELIVERY_WINDOW - timedelta(seconds=1))
    assert not delivery_open(START, START + DELIVERY_WINDOW)
    assert next_delivery(START, START, 1) == START + timedelta(minutes=1)
    late = START + DELIVERY_WINDOW - timedelta(hours=1)
    assert next_delivery(START, late, 9) == START + DELIVERY_WINDOW


def test_only_a_sale_to_a_buyer_with_an_rnc_or_cedula_has_one_to_deliver_to() -> None:
    assert buyer_rnc(fiscal_draft()) == "131098193"
    assert buyer_rnc(fiscal_draft(counterparty=None)) is None
    assert buyer_rnc(fiscal_draft(direction=Direction.PURCHASE)) is None


def test_dgii_publishes_its_directory_of_receivers_outside_certecf_only() -> None:
    assert [environment.has_directory for environment in Environment] == [True, False, True]
