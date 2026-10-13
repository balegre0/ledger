"""Options that a mistake in the code or the configuration can get wrong: one error shape, shared validators.

A module that takes options derives its own error from ``InvalidOptionError``, so that a caller can tell which
module refused and the error keeps a stable ``code`` of its own, and checks its options with the functions
here. The message always has the same shape: what the option belongs to, its name, what it must be, and what
it was (``described``: the type first, the value cut short).
"""

from math import inf
from typing import ClassVar

from ledger_kernel.errors import LedgerError, described


class InvalidOptionError(LedgerError):
    """An option that cannot work. A mistake in the code or the configuration, never client input."""

    code: ClassVar[str] = "invalid_option"
    label: ClassVar[str] = "option"
    """What the message calls the thing the option belongs to: ``outbox option``, ``stream`` and so on."""

    def __init__(self, *, option: str, requirement: str, value: object) -> None:
        super().__init__(f"{self.label} {option} must be {requirement}, not {described(value)}")


def is_positive_and_finite(value: float) -> bool:
    """Whether ``value`` is a number above zero and below infinity (so neither zero, NaN nor infinite)."""
    return 0 < value < inf


def require_at_least(error: type[InvalidOptionError], option: str, value: int, minimum: int = 1) -> None:
    """Refuse a ``value`` below ``minimum``, as ``error``."""
    if value < minimum:
        raise error(option=option, requirement=f"at least {minimum}", value=value)


def require_seconds(
    error: type[InvalidOptionError],
    option: str,
    value: float,
    *,
    floor: float | None = None,
    ceiling: float | None = None,
) -> None:
    """Refuse a wait that is not a positive, finite number of seconds within ``floor`` and ``ceiling``.

    The bounds are inclusive; a bound left out is not checked.
    """
    if not is_positive_and_finite(value):
        raise error(option=option, requirement="a positive, finite number of seconds", value=value)
    if floor is not None and value < floor:
        raise error(option=option, requirement=f"at least {floor} seconds", value=value)
    if ceiling is not None and value > ceiling:
        raise error(option=option, requirement=f"at most {ceiling} seconds", value=value)
