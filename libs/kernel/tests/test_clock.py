from datetime import UTC

from ledger_kernel.clock import utc_now


def test_utc_now_is_aware_and_in_utc() -> None:
    assert utc_now().tzinfo is UTC
