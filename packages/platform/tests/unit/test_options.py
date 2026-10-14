from math import inf, isfinite, nan

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_kernel.errors import ErrorKind
from ledger_platform.config.options import (
    InvalidOptionError,
    is_positive_and_finite,
    require_at_least,
    require_seconds,
)

WIDGET = "widget"


def test_the_message_says_what_the_option_belongs_to_what_it_must_be_and_what_it_was() -> None:
    error = InvalidOptionError(component=WIDGET, option="size", requirement="at least 3", value=2)
    assert str(error) == "widget option size must be at least 3, not int: 2"
    assert (error.kind, error.code, error.component) == (ErrorKind.INTERNAL, "invalid_option", WIDGET)


def test_a_value_is_cut_short_and_never_echoed_whole() -> None:
    error = InvalidOptionError(component=WIDGET, option="name", requirement="short", value="x" * 5000)
    assert len(str(error)) < 200


@given(value=st.floats())
def test_positive_and_finite_is_exactly_that(value: float) -> None:
    assert is_positive_and_finite(value) == (value > 0 and isfinite(value))


@pytest.mark.parametrize(("value", "minimum"), [(1, 1), (5, 1), (0, 0), (-3, -3), (10, 7)])
def test_a_value_at_or_above_the_minimum_is_accepted(value: int, minimum: int) -> None:
    require_at_least(WIDGET, "size", value, minimum)


@pytest.mark.parametrize(("value", "minimum"), [(0, 1), (-1, 1), (6, 7)])
def test_a_value_below_the_minimum_is_refused_for_its_component(value: int, minimum: int) -> None:
    with pytest.raises(
        InvalidOptionError, match=f"^widget option size must be at least {minimum}, not int: {value}"
    ):
        require_at_least(WIDGET, "size", value, minimum)


def test_the_minimum_is_one_unless_said_otherwise() -> None:
    with pytest.raises(InvalidOptionError, match="at least 1,") as caught:
        require_at_least(WIDGET, "size", 0)
    assert caught.value.component == WIDGET


@pytest.mark.parametrize("value", [0, 0.0, -1, -0.5, nan, inf, -inf])
def test_a_wait_that_is_not_a_positive_finite_number_is_refused(value: float) -> None:
    with pytest.raises(
        InvalidOptionError, match="widget option wait must be a positive, finite number of seconds"
    ):
        require_seconds(WIDGET, "wait", value)


@pytest.mark.parametrize("value", [0.001, 1, 30.0, 1e12])
def test_any_positive_finite_wait_is_accepted_when_no_bound_is_given(value: float) -> None:
    require_seconds(WIDGET, "wait", value)


def test_the_bounds_are_inclusive() -> None:
    require_seconds(WIDGET, "wait", 2.0, floor=2.0, ceiling=2.0)
    require_seconds(WIDGET, "wait", 1.5, floor=1.0, ceiling=3.0)


@pytest.mark.parametrize(
    ("value", "requirement"), [(0.5, "at least 1.0 seconds"), (3.5, "at most 3.0 seconds")]
)
def test_a_wait_outside_its_bounds_is_refused_and_says_which(value: float, requirement: str) -> None:
    with pytest.raises(InvalidOptionError, match=f"wait must be {requirement}"):
        require_seconds(WIDGET, "wait", value, floor=1.0, ceiling=3.0)
