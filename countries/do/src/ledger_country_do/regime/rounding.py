"""DGII's rounding and tolerance (IT §13 "Regla de Redondeo", §12 "Regla de tolerancia. Cuadratura").

Amounts carry two decimals, the third rounding half up (750.5212 -> 750.52, 750.5276 -> 750.53); unit prices
carry up to four. A line's amount is rounded, and each ITBIS rate's tax is computed on the sum of its lines
and rounded once: in the kernel's terms, the regime rounds at each category and at the document, never each
line's tax. Each detail line may differ from quantity x price by one unit, and the document's total by as
many units as it has lines; beyond that DGII answers *Aceptado Condicional*, which is valid but flagged.

The kernel's ``RoundingRule`` holds the mode, the points, the places and the line tolerance; what DGII adds
(unit prices' places, the per-line global tolerance, the source) is the pack's own data, here.
"""

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_country_do.regime.sources import TECHNICAL_REPORT
from ledger_kernel.money import RoundingMode
from ledger_kernel.regimes import RoundingPoint, RoundingRule

ROUNDING: Final = RoundingRule(
    mode=RoundingMode.HALF_UP,
    points=frozenset({RoundingPoint.CATEGORY, RoundingPoint.DOCUMENT}),
    places=2,
    line_tolerance=Decimal(1),
)
UNIT_PRICE_PLACES: Final = 4
GLOBAL_TOLERANCE_PER_LINE: Final = Decimal(1)
ROUNDING_SOURCE: Final = TECHNICAL_REPORT
DECIMAL_ROUNDING: Final[Mapping[RoundingMode, str]] = MappingProxyType(
    {
        RoundingMode.HALF_UP: ROUND_HALF_UP,
        RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
        RoundingMode.DOWN: ROUND_DOWN,
        RoundingMode.UP: ROUND_UP,
    }
)
"""Each of the kernel's rounding modes as ``decimal`` spells it."""


def round_with(rule: RoundingRule, value: Decimal) -> Decimal:
    """``value`` to ``rule``'s places, in ``rule``'s mode."""
    return value.quantize(Decimal(1).scaleb(-rule.places), rounding=DECIMAL_ROUNDING[rule.mode])


def round_amount(value: Decimal) -> Decimal:
    """``value`` to two places, half up: ``round_with(ROUNDING, value)``."""
    return round_with(ROUNDING, value)


def within_line_tolerance(declared: Decimal, *, quantity: Decimal, unit_price: Decimal) -> bool:
    """Whether a line's declared amount is within one unit of quantity x price."""
    return abs(declared - quantity * unit_price) <= ROUNDING.line_tolerance


def within_global_tolerance(declared: Decimal, *, computed: Decimal, lines: int) -> bool:
    """Whether a document's declared total is within as many units of the computed one as it has lines."""
    return abs(declared - computed) <= GLOBAL_TOLERANCE_PER_LINE * lines
