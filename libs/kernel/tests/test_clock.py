from datetime import UTC, datetime

from ledger_kernel.clock import utc_now


def test_utc_now_is_aware_and_in_utc() -> None:
    assert utc_now().tzinfo is UTC


def test_utc_now_reads_the_current_time() -> None:
    before = datetime.now(UTC)
    instant = utc_now()
    after = datetime.now(UTC)
    assert before <= instant <= after
