"""An e-CF's totals, computed as DGII computes them (FMT, Totales; IT §13).

A line's amount (``MontoItem``) is quantity x unit price, rounded half up to two places. Each ITBIS rate's
base (``MontoGravadoI1``..``I3``) is the sum of its lines' amounts, and its ITBIS (``TotalITBIS1``..``3``) the
base times the rate in force on the issue date, rounded. The total (``MontoTotal``) is the taxed bases, the
exempt amount and the ITBIS. Discounts, surcharges, withholdings and additional taxes are v2-M3's.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import final
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    AMOUNT_LIMIT,
    CATEGORY_OF,
    INTEGER_DIGITS,
    TAXED,
    BillingIndicator,
    DraftInvalidError,
)
from ledger_country_do.channels.ecf_v1.domain.errors import from_regime
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import rate
from ledger_country_do.regime.rounding import round_with
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_kernel.regimes import RegimeDef

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)


@final
@dataclass(frozen=True, slots=True)
class RateGroup:
    """The lines taxed at one ITBIS rate: their base, the rate, the ITBIS."""

    indicator: BillingIndicator
    base: Decimal
    percent: Decimal
    itbis: Decimal


@final
@dataclass(frozen=True, slots=True)
class Totals:
    """A document's amounts: each line's, each rate's, the exempt amount, the total."""

    line_amounts: tuple[Decimal, ...]
    groups: tuple[RateGroup, ...]
    """The ITBIS rates the document uses, in DGII's order (1, 2, 3)."""
    exempt: Decimal | None
    total: Decimal

    def group(self, indicator: BillingIndicator) -> RateGroup | None:
        """The group of ``indicator``; ``None`` if no line is taxed at it."""
        return next((group for group in self.groups if group.indicator is indicator), None)

    @property
    def taxed_total(self) -> Decimal | None:
        """``MontoGravadoTotal``: the taxed bases; ``None`` without taxed lines."""
        return sum((group.base for group in self.groups), _ZERO) if self.groups else None

    @property
    def itbis_total(self) -> Decimal | None:
        """``TotalITBIS``; ``None`` without taxed lines."""
        return sum((group.itbis for group in self.groups), _ZERO) if self.groups else None


class _TotalTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("total", f"(MontoTotal) has at most {INTEGER_DIGITS} integer digits")


def _percent(regime: RegimeDef, indicator: BillingIndicator, day: date) -> Decimal:
    return from_regime(lambda: rate(regime, CATEGORY_OF[indicator], day))


def compute_totals(draft: EcfDraft, regime: RegimeDef = REGIME) -> Totals:
    """``draft``'s totals under ``regime``'s rates in force on its issue date, rounded by ``regime``'s rule.

    ``DraftInvalidError`` (field ``total``) when the total has more integer digits than DGII's schemas allow;
    ``RegimeNotInForceError`` for a draft dated before the regime's rates; ``RegimeDefectError`` for a regime
    lacking what it needs.
    """
    rule = regime.rounding
    amounts = tuple(round_with(rule, line.quantity * line.unit_price) for line in draft.lines)
    groups: list[RateGroup] = []
    for indicator in TAXED:
        billed = [
            amount for line, amount in zip(draft.lines, amounts, strict=True) if line.billing is indicator
        ]
        if billed:
            base = sum(billed, _ZERO)
            percent = _percent(regime, indicator, draft.issued_on)
            groups.append(RateGroup(indicator, base, percent, round_with(rule, base * percent / _HUNDRED)))
    exempt_lines = [
        amount
        for line, amount in zip(draft.lines, amounts, strict=True)
        if line.billing is BillingIndicator.EXEMPT
    ]
    exempt = sum(exempt_lines, _ZERO) if exempt_lines else None
    total = sum((group.base + group.itbis for group in groups), _ZERO) + (exempt or _ZERO)
    if total >= AMOUNT_LIMIT:
        raise _TotalTooLargeError
    return Totals(amounts, tuple(groups), exempt, total)
