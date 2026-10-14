"""Options that a mistake in the code or the configuration can get wrong: one error, shared validators.

Every refusal is an ``InvalidOptionError`` (code ``invalid_option``) naming the component the option belongs
to (``database``, ``token``, ``health``, ...): a caller tells which component refused by ``component``, and
the code stays one. The message always has the same shape: the component, the option's name, what it must be,
and what it was (``described``: the type first, the value cut short).
"""

from math import inf
from typing import ClassVar, Final

from ledger_kernel.errors import LedgerError, described


class InvalidOptionError(LedgerError):
    """An option that cannot work. A mistake in the code or the configuration, never client input."""

    code: ClassVar[str] = "invalid_option"

    def __init__(self, *, component: str, option: str, requirement: str, value: object) -> None:
        super().__init__(f"{component} option {option} must be {requirement}, not {described(value)}")
        self.component: Final = component
        """What the option belongs to: ``database``, ``token``, ``gRPC``, ..."""


def is_positive_and_finite(value: float) -> bool:
    """Whether ``value`` is a number above zero and below infinity (so neither zero, NaN nor infinite)."""
    return 0 < value < inf


def require_at_least(component: str, option: str, value: int, minimum: int = 1) -> None:
    """Refuse a ``value`` below ``minimum``, for ``component``."""
    if value < minimum:
        raise InvalidOptionError(
            component=component, option=option, requirement=f"at least {minimum}", value=value
        )


def require_seconds(
    component: str,
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
        raise InvalidOptionError(
            component=component,
            option=option,
            requirement="a positive, finite number of seconds",
            value=value,
        )
    if floor is not None and value < floor:
        raise InvalidOptionError(
            component=component, option=option, requirement=f"at least {floor} seconds", value=value
        )
    if ceiling is not None and value > ceiling:
        raise InvalidOptionError(
            component=component, option=option, requirement=f"at most {ceiling} seconds", value=value
        )
