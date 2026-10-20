"""An e-CF's totals, computed as DGII computes them (FMT, Totales and OtraMoneda; IT §12 and §13).

A line's amount (``MontoItem``) is quantity x unit price less its discount, rounded half up to two places.
Each ITBIS rate's base (``MontoGravadoI1``..``I3``) is the sum of its lines' amounts, and its ITBIS
(``TotalITBIS1``..``3``) the base times the rate in force on the issue date, rounded. The total
(``MontoTotal``) is the taxed bases, the exempt amount and the ITBIS. What a purchase withholds
(``TotalITBISRetenido``, ``TotalISRRetencion``) is the sum of its lines' withholdings; a line withholds at
most its own ITBIS (its amount at its rate, rounded) and at most its amount as ISR.

A draft in another currency is computed in its own currency (``OtraMoneda``, which keeps it), and in pesos
(``Totales``, which DGII reads and checks) by the same arithmetic from each line's figures in pesos: its unit
price at the exchange rate rounded half up to four places (``PrecioUnitarioItem``), its discount at the rate
rounded to two (``DescuentoMonto``), its amount quantity x that price less that discount, rounded; then the
bases, ITBIS and total as above. So every relation DGII checks in pesos holds. A line whose amount in pesos
so computed strays from its amount at the exchange rate by more than ``CONVERSION_TOLERANCE`` (a price of
many decimals in a large quantity: 100,000 units at USD 0.0001) is refused: its pesos would not be its
value. Invoicing converts each category's base and tax once, so its functional totals and these pesos
differ by at most that per line, plus the ITBIS on it, which ``mapping.verify_totals``' tolerance of one
unit per line absorbs. A line's withholdings in pesos are converted once, never above the line's ITBIS or
amount in pesos; one that withholds the whole ITBIS (or the whole amount as ISR) withholds it in pesos too.
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
from ledger_country_do.regime.rounding import (
    DECIMAL_ROUNDING,
    GLOBAL_TOLERANCE_PER_LINE,
    UNIT_PRICE_PLACES,
    round_with,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft, Line
lazy from ledger_kernel.regimes import RegimeDef, RoundingRule

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)
_PRICE_QUANTUM = Decimal(1).scaleb(-UNIT_PRICE_PLACES)
CONVERSION_TOLERANCE = GLOBAL_TOLERANCE_PER_LINE / 2
"""How far a line's amount in pesos (from its price in pesos) may stray from its value at the exchange rate:
half of DGII's unit per line, so that with the ITBIS on it (18 % at most) the document's pesos stay within
one unit per line of invoicing's, which converts each category once."""
CONVERSION_REQUIREMENT = (
    "in pesos, four decimals, times the quantity, is within half a unit of the line's value"
)


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
    """A line's figures in one currency, as its ``Item`` writes them, and its own ITBIS."""

    unit_price: Decimal
    discount: Decimal
    amount: Decimal
    itbis: Decimal
    """The line's ITBIS alone (its amount at its rate, rounded): what it may withhold at most."""
    itbis_withheld: Decimal | None = None
    isr_withheld: Decimal | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PesoLine:
    """A line of another currency in pesos: its unit price, discount and amount, and how far it strays."""

    unit_price: Decimal
    discount: Decimal
    amount: Decimal
    gap: Decimal
    """``|amount - (quantity x unit price - discount) x exchange rate|``."""


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
    """The same document in its own currency (``OtraMoneda``) when it is not the peso."""

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
        """``TotalITBISRetenido`` (or perceived); ``None`` when no line withholds ITBIS."""
        return _withheld([line.itbis_withheld for line in self.lines])

    @property
    def isr_withheld(self) -> Decimal | None:
        """``TotalISRRetencion`` (or perceived); ``None`` when no line withholds ISR."""
        return _withheld([line.isr_withheld for line in self.lines])


class _TotalTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("total", f"(MontoTotal) has at most {INTEGER_DIGITS} integer digits")


class _PriceTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.unit_price", f"has at most {INTEGER_DIGITS} integer digits in pesos")


class _ConversionInexactError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.unit_price", CONVERSION_REQUIREMENT)


class _ItbisWithheldTooLargeError(DraftInvalidError):
    def __init__(self) -> None:
        super().__init__("line.itbis_withheld", ITBIS_ABOVE_TAX.requirement)


def peso_price(unit_price: Decimal, exchange_rate: Decimal, rule: RoundingRule = REGIME.rounding) -> Decimal:
    """``unit_price`` at ``exchange_rate``, to a unit price's four places in ``rule``'s mode."""
    return (unit_price * exchange_rate).quantize(_PRICE_QUANTUM, rounding=DECIMAL_ROUNDING[rule.mode])


def peso_line(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    discount: Decimal,
    exchange_rate: Decimal,
    rule: RoundingRule = REGIME.rounding,
) -> PesoLine:
    """A line of another currency in pesos, as DGII checks it (see the module); its price below 10^16."""
    price = peso_price(unit_price, exchange_rate, rule)
    discounted = round_with(rule, discount * exchange_rate)
    amount = round_with(rule, max(quantity * price - discounted, _ZERO))  # a whole discount: never -0.00
    gap = abs(amount - (quantity * unit_price - discount) * exchange_rate)
    return PesoLine(unit_price=price, discount=discounted, amount=amount, gap=gap)


def _percent(regime: RegimeDef, indicator: BillingIndicator, day: date) -> Decimal:
    return from_regime(lambda: rate(regime, CATEGORY_OF[indicator], day))


def _summed(
    lines: tuple[LineFigures, ...],
    draft: EcfDraft,
    percents: dict[BillingIndicator, Decimal],
    rule: RoundingRule,
) -> tuple[tuple[RateGroup, ...], Decimal | None, Decimal]:
    """The rate groups, exempt amount and total of ``lines`` (``draft``'s, in one currency)."""
    groups: list[RateGroup] = []
    for indicator, percent in percents.items():
        billed = (
            figures.amount
            for line, figures in zip(draft.lines, lines, strict=True)
            if line.billing is indicator
        )
        base = sum(billed, _ZERO)
        groups.append(RateGroup(indicator, base, percent, round_with(rule, base * percent / _HUNDRED)))
    exempt_lines = [
        figures.amount
        for line, figures in zip(draft.lines, lines, strict=True)
        if line.billing is BillingIndicator.EXEMPT
    ]
    exempt = sum(exempt_lines, _ZERO) if exempt_lines else None
    total = sum((group.base + group.itbis for group in groups), _ZERO) + (exempt or _ZERO)
    if total >= AMOUNT_LIMIT:
        raise _TotalTooLargeError
    return tuple(groups), exempt, total


def _own(draft: EcfDraft, regime: RegimeDef, percents: dict[BillingIndicator, Decimal]) -> Totals:
    """``draft``'s totals in its own currency."""
    rule = regime.rounding
    lines: list[LineFigures] = []
    for line in draft.lines:
        amount = round_with(rule, line.amount)
        itbis = round_with(rule, amount * percents.get(line.billing, _ZERO) / _HUNDRED)
        if line.itbis_withheld is not None and line.itbis_withheld > itbis:
            raise _ItbisWithheldTooLargeError
        lines.append(
            LineFigures(
                unit_price=line.unit_price,
                discount=line.discount,
                amount=amount,
                itbis=itbis,
                itbis_withheld=line.itbis_withheld,
                isr_withheld=line.isr_withheld,
            )
        )
    figures = tuple(lines)
    groups, exempt, total = _summed(figures, draft, percents, rule)
    return Totals(lines=figures, groups=groups, exempt=exempt, total=total)


def _withheld_in_pesos(
    own: Decimal | None, whole: Decimal, whole_in_pesos: Decimal, converted: Decimal
) -> Decimal | None:
    """A withholding of ``own`` out of ``whole``, in pesos: all of ``whole_in_pesos`` when it was all."""
    if own is None:
        return None
    return whole_in_pesos if own == whole else min(converted, whole_in_pesos)


def _line_in_pesos(
    line: Line, own: LineFigures, exchange_rate: Decimal, percent: Decimal, rule: RoundingRule
) -> LineFigures:
    """``line`` (``own`` in its currency) in pesos; refused when its pesos stray from its value."""
    if (
        line.unit_price * exchange_rate >= AMOUNT_LIMIT
        or peso_price(line.unit_price, exchange_rate, rule) >= AMOUNT_LIMIT
    ):
        raise _PriceTooLargeError
    pesos = peso_line(
        quantity=line.quantity,
        unit_price=line.unit_price,
        discount=line.discount,
        exchange_rate=exchange_rate,
        rule=rule,
    )
    if pesos.gap > CONVERSION_TOLERANCE:
        raise _ConversionInexactError
    itbis = round_with(rule, pesos.amount * percent / _HUNDRED)

    def converted(amount: Decimal | None) -> Decimal:
        return _ZERO if amount is None else round_with(rule, amount * exchange_rate)

    return LineFigures(
        unit_price=pesos.unit_price,
        discount=pesos.discount,
        amount=pesos.amount,
        itbis=itbis,
        itbis_withheld=_withheld_in_pesos(
            own.itbis_withheld, own.itbis, itbis, converted(own.itbis_withheld)
        ),
        isr_withheld=_withheld_in_pesos(
            own.isr_withheld, own.amount, pesos.amount, converted(own.isr_withheld)
        ),
    )


def compute_totals(draft: EcfDraft, regime: RegimeDef = REGIME) -> Totals:
    """``draft``'s totals in pesos under ``regime``'s rates in force on its issue date, rounded by its rule.

    ``DraftInvalidError`` when a total or a unit price in pesos has more integer digits than DGII's schemas
    allow (fields ``total``, ``line.unit_price``), a line's pesos stray from its value (``line.unit_price``)
    or a line withholds more ITBIS than it bears (``line.itbis_withheld``); ``RegimeNotInForceError`` for a
    draft dated before the regime's rates; ``RegimeDefectError`` for a regime lacking what it needs.
    """
    rule = regime.rounding
    billed = {line.billing for line in draft.lines}
    percents: dict[BillingIndicator, Decimal] = {
        indicator: _percent(regime, indicator, draft.issued_on) for indicator in TAXED if indicator in billed
    }
    own = _own(draft, regime, percents)
    exchange_rate = draft.exchange_rate  # given exactly for a currency other than the peso (``EcfDraft``)
    if exchange_rate is None:
        return own
    if own.total * exchange_rate >= AMOUNT_LIMIT:  # before the lines: a total too large is what is wrong
        raise _TotalTooLargeError
    lines = tuple(
        _line_in_pesos(line, figures, exchange_rate, percents.get(line.billing, _ZERO), rule)
        for line, figures in zip(draft.lines, own.lines, strict=True)
    )
    groups, exempt, total = _summed(lines, draft, percents, rule)
    return Totals(lines=lines, groups=groups, exempt=exempt, total=total, foreign=own)
