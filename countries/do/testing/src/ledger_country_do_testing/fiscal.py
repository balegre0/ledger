"""Fiscal drafts as ``fiscal`` builds them from an invoice, for the channel's tests.

Each line is priced as invoicing prices it: its base is ``quantity x unit_price - discount`` rounded half up
to the currency, each tax is the kernel calculator's for that line alone, the totals are the calculator's over
every line at the tax point, and a document in another currency than the peso carries its totals in pesos at
the exchange rate (each category's base and tax and the net converted once, rounded as the regime rounds).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, TypedDict, Unpack, final
from uuid import UUID
lazy from collections.abc import Mapping

from ledger_country_do.regime import REGIME
from ledger_fiscal_kit.drafts import (
    DocumentRef,
    FiscalDraft,
    FiscalLine,
    FiscalLineTax,
    FiscalParty,
)
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.money import Currency, Money
from ledger_kernel.regimes import (
    CategoryTotal,
    Direction,
    DocumentKind,
    TaxCalculator,
    TaxCategoryKey,
    TaxLine,
    Totals,
)
from ledger_kernel.tax_ids import TaxId, TaxIdScheme
from ledger_kernel.tenancy import Mode
lazy from ledger_fiscal_kit.drafts import FieldValue, FiscalReference

PESO: Final = Currency.of("DOP")
ORGANIZATION: Final = OrganizationId(UUID("0199e3a0-0000-7000-8000-0000000000d1"))
COMPANY: Final = CompanyId(UUID("0199e3a0-0000-7000-8000-0000000000d2"))
DOCUMENT: Final = DocumentRef(UUID("0199e3a0-0000-7000-8000-0000000000d3"))
ISSUED_ON: Final = date(2026, 10, 13)
ISSUER: Final = FiscalParty(
    tax_id=TaxId(TaxIdScheme("do.rnc"), "101850043"),
    legal_name="FERRETERIA DUARTE SRL",
    address="AV. DUARTE 100, SANTO DOMINGO",
)
BUYER: Final = FiscalParty(
    tax_id=TaxId(TaxIdScheme("do.rnc"), "131098193"),
    legal_name="CONSTRUCTORA DEL CARIBE SRL",
    address="CALLE 5 #12, SANTIAGO",
)
STANDARD: Final = TaxCategoryKey("do.itbis.standard")
REDUCED: Final = TaxCategoryKey("do.itbis.reduced")
ZERO: Final = TaxCategoryKey("do.itbis.zero")
EXEMPT: Final = TaxCategoryKey("do.itbis.exempt")
_CALCULATOR: Final = TaxCalculator(REGIME)
_KINDS: Final = MappingProxyType({category.key: category.kind for category in REGIME.tax_categories})
_NO_FIELDS: Final[Mapping[str, FieldValue]] = MappingProxyType({})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Priced:
    """A line as a client sends it: what it is, how many, at what price, less what discount, taxed how."""

    unit_price: Decimal
    quantity: Decimal = Decimal(1)
    discount: Decimal = Decimal(0)
    category: TaxCategoryKey = STANDARD
    description: str = "CEMENTO GRIS 42.5 KG"
    goods: bool | None = None


def _line(number: int, priced: Priced, currency: Currency, tax_point: date) -> FiscalLine:
    gross = Money(priced.unit_price, currency) * priced.quantity
    base = (gross - Money(priced.discount, currency)).rounded()
    (alone,) = _CALCULATOR.totals([TaxLine(base=base, categories=(priced.category,))], tax_point).categories
    rate = next(each for each in REGIME.tax_categories if each.key == priced.category).rates
    return FiscalLine(
        number=number,
        description=priced.description,
        quantity=priced.quantity,
        unit_price=priced.unit_price,
        discount=priced.discount,
        base=base,
        taxes=(
            FiscalLineTax(
                category=priced.category,
                kind=_KINDS[priced.category],
                percent=alone.percent,
                since=rate.entries[0].since,
                amount=alone.tax,
            ),
        ),
        goods=priced.goods,
    )


def converted(totals: Totals, rate: Decimal) -> Totals:
    """``totals`` in pesos at ``rate``, as invoicing converts functional totals."""
    rule = REGIME.rounding

    def peso(amount: Money) -> Money:
        return Money(amount.times(rate, rule.places, rule.mode).amount, PESO)

    categories = tuple(
        CategoryTotal(category=each.category, percent=each.percent, base=peso(each.base), tax=peso(each.tax))
        for each in totals.categories
    )
    net = peso(totals.net)
    tax = sum((each.tax for each in categories), start=Money.zero(PESO))
    return Totals(net=net, categories=categories, tax=tax, gross=net + tax)


class DraftOptions(TypedDict, total=False):
    """What a test changes of a draft: by default a sales invoice in pesos, by ``ISSUER`` to ``BUYER``."""

    kind: DocumentKind
    direction: Direction
    counterparty: FiscalParty | None
    fields: Mapping[str, FieldValue]
    currency: Currency
    exchange_rate: Decimal | None
    """Gives the draft its totals in pesos (its functional totals)."""
    references: tuple[FiscalReference, ...]
    issuer: FiscalParty


def fiscal_draft(*lines: Priced, **options: Unpack[DraftOptions]) -> FiscalDraft:
    """A draft of ``lines`` (one of 1,000.00 at 18 % when none), priced and totalled as invoicing does."""
    priced = lines or (Priced(unit_price=Decimal("1000.00")),)
    currency = options.get("currency", PESO)
    exchange_rate = options.get("exchange_rate")
    fiscal_lines = tuple(
        _line(number, each, currency, ISSUED_ON) for number, each in enumerate(priced, start=1)
    )
    totals = _CALCULATOR.totals(
        [
            TaxLine(base=line.base, categories=tuple(tax.category for tax in line.taxes))
            for line in fiscal_lines
        ],
        ISSUED_ON,
    )
    return FiscalDraft(
        document=DOCUMENT,
        version=1,
        organization=ORGANIZATION,
        company=COMPANY,
        mode=Mode.TEST,
        channel="do-ecf-v1",
        environment="testecf",
        regime=REGIME.key,
        kind=options.get("kind", DocumentKind.INVOICE),
        direction=options.get("direction", Direction.SALES),
        issuer=options.get("issuer", ISSUER),
        establishment_code="001",
        time_zone=REGIME.time_zone,
        counterparty=options.get("counterparty", BUYER),
        lines=fiscal_lines,
        totals=totals,
        currency=currency,
        exchange_rate=exchange_rate,
        functional_totals=None if exchange_rate is None else converted(totals, exchange_rate),
        references=options.get("references", ()),
        fields=options.get("fields", _NO_FIELDS),
        issue_date=ISSUED_ON,
        tax_point=ISSUED_ON,
    )
