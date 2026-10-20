"""A fiscal number, as a channel's numbering policy allocates it."""

import inspect
from dataclasses import replace
from datetime import date
from typing import Final, final, override

import pytest

from ledger_fiscal_kit.numbering import FiscalNumber, NumberingPolicy
from ledger_kernel.errors import InvalidValueError
lazy from ledger_fiscal_kit.drafts import FiscalDraft
lazy from ledger_platform.data.unit_of_work import UnitOfWork

NUMBER: Final = FiscalNumber(text="E310000000001", series="E31", sequence=1, expires_on=date(2027, 12, 31))


@final
class Fixed(NumberingPolicy):
    """A policy that always allocates the same number: enough to be one."""

    @override
    async def allocate(self, uow: UnitOfWork, draft: FiscalDraft, *, series: str) -> FiscalNumber:
        return replace(NUMBER, series=series)


def test_a_number_keeps_its_text_series_sequence_and_expiry() -> None:
    assert (NUMBER.text, NUMBER.series, NUMBER.sequence, NUMBER.expires_on) == (
        "E310000000001",
        "E31",
        1,
        date(2027, 12, 31),
    )
    assert replace(NUMBER, expires_on=None).expires_on is None


def test_a_policy_allocates_in_a_unit_for_a_draft_and_a_named_series() -> None:
    assert NumberingPolicy.__abstractmethods__ == frozenset({"allocate"})
    parameters = inspect.signature(Fixed().allocate).parameters
    assert list(parameters) == ["uow", "draft", "series"]
    assert parameters["series"].kind is inspect.Parameter.KEYWORD_ONLY


def test_a_sequence_starts_at_one() -> None:
    with pytest.raises(InvalidValueError, match=r"fiscal_number.sequence must be 1 or more"):
        replace(NUMBER, sequence=0)


@pytest.mark.parametrize(
    "value", ["", "E31 0001", "x" * 65, "E31\n"], ids=["empty", "space", "long", "newline"]
)
def test_a_number_has_a_plain_text_and_series(value: str) -> None:
    with pytest.raises(InvalidValueError, match=r"fiscal_number.text must be 1 to 64 printable characters"):
        replace(NUMBER, text=value)
    with pytest.raises(InvalidValueError, match=r"fiscal_number.series must be 1 to 64 printable characters"):
        replace(NUMBER, series=value)
