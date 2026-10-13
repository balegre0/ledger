"""DGII's rounding and tolerance (IT §13 "Regla de Redondeo", §12 "Regla de tolerancia. Cuadratura").

Amounts carry two decimals, the third rounding half up (750.5212 -> 750.52, 750.5276 -> 750.53); unit prices
carry up to four. Each detail line may differ from quantity x price by one unit, and the document's total
by as many units as it has lines; beyond that DGII answers *Aceptado Condicional*, which is valid but flagged.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from ledger_country_do.regime.definitions import RoundingRule
from ledger_country_do.regime.sources import TECHNICAL_REPORT

ROUNDING: Final = RoundingRule(
    mode=ROUND_HALF_UP,
    places=2,
    unit_price_places=4,
    line_tolerance=Decimal(1),
    global_tolerance_per_line=Decimal(1),
    source=TECHNICAL_REPORT,
)
CENT: Final = Decimal("0.01")


def round_amount(value: Decimal) -> Decimal:
    """``value`` to two places, half up."""
    return value.quantize(CENT, rounding=ROUNDING.mode)


def within_line_tolerance(declared: Decimal, *, quantity: Decimal, unit_price: Decimal) -> bool:
    """Whether a line's declared amount is within one unit of quantity x price."""
    return abs(declared - quantity * unit_price) <= ROUNDING.line_tolerance


def within_global_tolerance(declared: Decimal, *, computed: Decimal, lines: int) -> bool:
    """Whether a document's declared total is within as many units of the computed one as it has lines."""
    return abs(declared - computed) <= ROUNDING.global_tolerance_per_line * lines
