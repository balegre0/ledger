from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from ledger_kernel.errors import InvalidValueError
from ledger_platform.jobs import Done, Park, RunAgain

LATER = datetime(2026, 10, 13, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("outcome", "field"), [(Done(), "at"), (RunAgain(LATER), "at"), (Park("certificate_missing"), "reason")]
)
def test_the_outcomes_are_frozen(outcome: object, field: str) -> None:
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(outcome, field, "changed")


def test_a_parked_job_says_why() -> None:
    with pytest.raises(InvalidValueError, match=r"park\.reason"):
        Park("")


def test_run_again_needs_an_aware_time() -> None:
    naive = datetime.fromisoformat("2026-10-13T12:00:00")
    with pytest.raises(InvalidValueError, match=r"run_again\.at"):
        RunAgain(naive)
