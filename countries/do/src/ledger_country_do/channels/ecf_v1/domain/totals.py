"""An e-CF's totals, computed as DGII computes them (FMT, Totales and OtraMoneda; IT §13).

A line's amount (``MontoItem``) is quantity x unit price less its discount, rounded half up to two places.
Each ITBIS rate's base (``MontoGravadoI1``..``I3``) is the sum of its lines' amounts, and its ITBIS
(``TotalITBIS1``..``3``) the base times the rate in force on the issue date, rounded. The total
(``MontoTotal``) is the taxed bases, the exempt amount and the ITBIS. What a purchase withholds
(``TotalITBISRetenido``, ``TotalISRRetencion``) is the sum of its lines' withholdings; a line withholds at
most its own ITBIS (its amount at its rate, rounded).

A draft in another currency is computed so in its own currency (``OtraMoneda``), and its totals in pesos
(``Totales``, which DGII reads) are those amounts at the exchange rate, each converted once and rounded half
up to two places: each line's amount, discount and withholdings, each rate's base and ITBIS, the exempt
amount; the total and the withheld totals are their sums. A line's unit price in pesos
(``PrecioUnitarioItem``) is its price at the exchange rate rounded half up to four places, the original kept
for ``OtraMonedaDetalle``. Invoicing converts a document's functional totals the same way per category (base
and tax), so each category agrees (both round the same product once); its gross converts the net once, so
the totals in pesos may differ by a cent per category, within DGII's tolerance (``mapping.verify_totals``).
Surcharges and additional taxes are not computed.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import final
lazy from datetime import date

from ledger_country_do.channels.ecf_v1.domain.drafts import (
    AMOUNT_LIMIT,
    CATEGORY_OF,
    INTEGER_DIGITS,
    ITBIS_ABOVE_TAX,
    TAXED,
    BillingIndicator,
    DraftInvalidError,
)
from ledger_country_do.channels.ecf_v1.domain.errors import from_regime
from ledger_country_do.regime import REGIME
from ledger_country_do.regime.entries import rate
from ledger_country_do.regime.rounding import DECIMAL_ROUNDING, UNIT_PRICE_PLACES, round_with
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft, Line
lazy from ledger_kernel.regimes import RegimeDef, RoundingRule

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_PRICE_QUANTUM = Decimal(1).scaleb(-UNIT_PRICE_PLACES)


@final
@dataclass(frozen=True, slots=True)
class RateGroup:
    """The lines taxed at one ITBIS rate: their base, the rate, the ITBIS."""

    indicator: BillingIndicator
    base: Decimal
    percent: Decimal
    itbis: Decimal


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LineFigures:
    """A line's figures in one currency, as its ``Item`` writes them."""

    unit_price: Decimal
    discount: Decimal
    amount: Decimal
    itbis_withheld: Decimal | None = None
    isr_withheld: Decimal | None = None


def _withheld(amounts: list[Decimal | None]) -> Decimal | None:
    given = [amount for amount in amounts if amount is not None]
    return sum(given, _ZERO) if given else None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Totals:
    """A document's amounts in pesos: each line's, each rate's, the exempt amount, the total, the withheld."""

    lines: tuple[LineFigures, ...]
    groups: tuple[RateGroup, ...]
    """The ITBIS rates the document uses, in DGII's order (1, 2, 3)."""
    exempt: Decimal | None
    total: Decimal
    foreign: Totals | None = None
    """The same amounts in the document's currency (``OtraMoneda``) when it is not the peso."""

    def group(self, indicator: BillingIndicator) -> RateGroup | None:
        """The group of ``indicator``; ``None`` if no line is taxed at it."""
        return next((group for group in self.groups if group.indicator is indicator), None)

    @property
    def line_amounts(self) -> tuple[Decimal, ...]:
        """Each line's amount (``MontoItem``)."""
        return tuple(line.amount for line in self.lines)

    @property
    def taxed_total(self) -> Decimal | None:
        """``MontoGravadoTotal``: the taxed bases; ``None`` without taxed lines."""
        return sum((group.base for group in self.groups), _ZERO) if self.groups else None

    @property
    def itbis_total(self) -> Decimal | None:
        """``TotalITBIS``; ``None`` without taxed lines."""
        return sum((group.itbis for group in self.groups), _ZERO) if self.groups else None

    @property
    def itbis_withheld(self) -> Decimal | None:
        """``TotalITBISRetenido``; ``None`` when no line withholds ITBIS."""
        return _withheld([line.itbis_withheld for line in self.lines])

    @property
    def isr_withheld(self) -> Decimal | None:
        """``TotalISRRetencion``; ``None`` when no line withholds ISR."""
        return _withheld([line.isr_withheld for line in self.lines])


class _TotalTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("total", f"(MontoTotal) has at most {INTEGER_DIGITS} integer digits")


class _PriceTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.unit_price", f"has at most {INTEGER_DIGITS} integer digits in pesos")


class _ItbisWithheldTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.itbis_withheld", ITBIS_ABOVE_TAX.requirement)


def _percent(regime: RegimeDef, indicator: BillingIndicator, day: date) -> Decimal:
    return from_regime(lambda: rate(regime, CATEGORY_OF[indicator], day))


def _within(totals: Totals) -> Totals:
    if totals.total >= AMOUNT_LIMIT:
        raise _TotalTooLargeError
    return totals


def _figures(line: Line, rule: RoundingRule, percents: dict[BillingIndicator, Decimal]) -> LineFigures:
    amount = round_with(rule, line.amount)
    percent = percents.get(line.billing, _ZERO)
    if line.itbis_withheld is not None and line.itbis_withheld > round_with(
        rule, amount * percent / _HUNDRED
    ):
        raise _ItbisWithheldTooLargeError
    return LineFigures(
        unit_price=line.unit_price,
        discount=line.discount,
        amount=amount,
        itbis_withheld=line.itbis_withheld,
        isr_withheld=line.isr_withheld,
    )


def _own(draft: EcfDraft, regime: RegimeDef) -> Totals:
    """``draft``'s totals in its own currency."""
    rule = regime.rounding
    billed = {line.billing for line in draft.lines}
    percents: dict[BillingIndicator, Decimal] = {
        indicator: _percent(regime, indicator, draft.issued_on) for indicator in TAXED if indicator in billed
    }
    lines = tuple(_figures(line, rule, percents) for line in draft.lines)
    groups: list[RateGroup] = []
    for indicator, percent in percents.items():
        base = sum(
            (
                figures.amount
                for line, figures in zip(draft.lines, lines, strict=True)
                if line.billing is indicator
            ),
            _ZERO,
        )
        groups.append(RateGroup(indicator, base, percent, round_with(rule, base * percent / _HUNDRED)))
    exempt_lines = [
        figures.amount
        for line, figures in zip(draft.lines, lines, strict=True)
        if line.billing is BillingIndicator.EXEMPT
    ]
    exempt = sum(exempt_lines, _ZERO) if exempt_lines else None
    total = sum((group.base + group.itbis for group in groups), _ZERO) + (exempt or _ZERO)
    return _within(Totals(lines=lines, groups=tuple(groups), exempt=exempt, total=total))


def peso_price(unit_price: Decimal, exchange_rate: Decimal, rule: RoundingRule = REGIME.rounding) -> Decimal:
    """``unit_price`` at ``exchange_rate``, to a unit price's four places in ``rule``'s mode."""
    return (unit_price * exchange_rate).quantize(_PRICE_QUANTUM, rounding=DECIMAL_ROUNDING[rule.mode])


def _in_pesos(own: Totals, exchange_rate: Decimal, rule: RoundingRule) -> Totals:
    """``own`` at ``exchange_rate``, each amount converted once and rounded by ``rule``."""

    def peso(amount: Decimal) -> Decimal:
        return round_with(rule, amount * exchange_rate)

    def maybe(amount: Decimal | None) -> Decimal | None:
        return None if amount is None else peso(amount)

    def price(amount: Decimal) -> Decimal:
        converted = peso_price(amount, exchange_rate, rule)
        if converted >= AMOUNT_LIMIT:
            raise _PriceTooLargeError
        return converted

    groups = tuple(
        RateGroup(group.indicator, peso(group.base), group.percent, peso(group.itbis)) for group in own.groups
    )
    exempt = maybe(own.exempt)
    total = sum((group.base + group.itbis for group in groups), _ZERO) + (exempt or _ZERO)
    if total >= AMOUNT_LIMIT:  # before the prices: a total too large is the first thing wrong
        raise _TotalTooLargeError
    lines = tuple(
        LineFigures(
            unit_price=price(line.unit_price),
            discount=peso(line.discount),
            amount=peso(line.amount),
            itbis_withheld=maybe(line.itbis_withheld),
            isr_withheld=maybe(line.isr_withheld),
        )
        for line in own.lines
    )
    return Totals(lines=lines, groups=groups, exempt=exempt, total=total, foreign=own)


def compute_totals(draft: EcfDraft, regime: RegimeDef = REGIME) -> Totals:
    """``draft``'s totals in pesos under ``regime``'s rates in force on its issue date, rounded by its rule.

    ``DraftInvalidError`` when a total or a unit price in pesos has more integer digits than DGII's schemas
    allow (fields ``total``, ``line.unit_price``) or a line withholds more ITBIS than it bears
    (``line.itbis_withheld``); ``RegimeNotInForceError`` for a draft dated before the regime's rates;
    ``RegimeDefectError`` for a regime lacking what it needs.
    """
    own = _own(draft, regime)
    exchange_rate = draft.exchange_rate  # given exactly for a currency other than the peso (``EcfDraft``)
    return own if exchange_rate is None else _in_pesos(own, exchange_rate, regime.rounding)
