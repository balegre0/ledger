"""A document's lines as the client wrote them (Decision 4): what is sold, how much, at what price, taxed how.

Numbers are exact ``Decimal`` values, bounded so that money can hold them: a quantity above 0 with at most 6
decimal places, a unit price and a discount (an amount, never a percentage) from 0 with at most 12, each below
10^18. A line is checked where its position is known, by the terms that hold it (``LineTerms.checked``);
which tax categories it names is checked against the company's regime when it is priced
(``pricing.price``), as is a discount above the line's gross.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, final

from ledger_invoicing.domain.errors import InvalidLineError
from ledger_invoicing.domain.text import parse_description
from ledger_kernel.money import MAX_INTEGER_DIGITS

MAX_QUANTITY_PLACES: Final = 6
MAX_PRICE_PLACES: Final = 12
"""A unit price's and a discount's places: a 12-place price times a 6-place quantity fits money's 24."""
_ZERO: Final = Decimal(0)
_AMOUNT: Final = f"a finite number from 0 and below 10^18, with at most {MAX_PRICE_PLACES} decimal places"
_QUANTITY: Final = (
    f"a finite number above 0 and below 10^18, with at most {MAX_QUANTITY_PLACES} decimal places"
)


def places(value: Decimal) -> int:
    """The decimal places ``value`` is written with (``18.50`` has two, ``1E+1`` none)."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def bounded(value: Decimal, *, places_at_most: int, above_zero: bool) -> bool:
    """Whether ``value`` is finite, from (or above) 0, below 10^18, with at most ``places_at_most`` places."""
    return (
        value.is_finite()
        and (value > _ZERO if above_zero else value >= _ZERO)
        and value.adjusted() < MAX_INTEGER_DIGITS
        and places(value) <= places_at_most
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LineTerms:
    """A line as the client wrote it; ``taxes`` are category keys of the company's regime, in its order."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = _ZERO
    taxes: tuple[str, ...]

    def checked(self, position: int) -> LineTerms:
        """The line at ``position`` of its document, its description cleaned, once its values keep the rules.

        A value that breaks one is refused by its path (``lines.3.quantity``), never repeated
        (``InvalidLineError``).
        """
        if not bounded(self.quantity, places_at_most=MAX_QUANTITY_PLACES, above_zero=True):
            raise InvalidLineError(position, "quantity", _QUANTITY)
        if not bounded(self.unit_price, places_at_most=MAX_PRICE_PLACES, above_zero=False):
            raise InvalidLineError(position, "unit_price", _AMOUNT)
        if not bounded(self.discount, places_at_most=MAX_PRICE_PLACES, above_zero=False):
            raise InvalidLineError(position, "discount", _AMOUNT)
        if not self.taxes:
            raise InvalidLineError(position, "taxes", "one or more tax categories")
        return replace(
            self, description=parse_description(self.description, field=f"lines.{position}.description")
        )
