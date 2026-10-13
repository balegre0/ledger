"""ITBIS, the Dominican VAT: its categories and their dated rates (FMT, Totales: tasas 18 %, 16 %, 0 %).

The timelines start on ``COVERED_FROM`` (2019-01-01, do-ecf-v1's ``valid_from``): every e-CF falls after it,
and the rates' earlier history (18 % from Ley 253-12; 16 % reached in phases from 2013 to 2016) is not
asserted. A rate change is a new ``Since`` at the head of a timeline, never an edit of an old one.
"""

from decimal import Decimal
from typing import Final

from ledger_country_do.regime.definitions import GenericCategory, Since, TaxCategoryDef, Timeline
from ledger_country_do.regime.sources import COVERED_FROM, FORMAT_ECF


def _percent(key: str, percent: int) -> Timeline[Decimal]:
    return Timeline(key, (Since(COVERED_FROM, Decimal(percent)),))


STANDARD: Final = TaxCategoryDef(
    key="do.itbis.standard",
    label="ITBIS 18 %",
    generic=GenericCategory.STANDARD,
    rates=_percent("do.itbis.standard", 18),
    source=FORMAT_ECF,
)
REDUCED: Final = TaxCategoryDef(
    key="do.itbis.reduced",
    label="ITBIS 16 %",
    generic=GenericCategory.REDUCED,
    rates=_percent("do.itbis.reduced", 16),
    source=FORMAT_ECF,
)
ZERO: Final = TaxCategoryDef(
    key="do.itbis.zero",
    label="ITBIS 0 %",
    generic=GenericCategory.ZERO,
    rates=_percent("do.itbis.zero", 0),
    source=FORMAT_ECF,
)
EXEMPT: Final = TaxCategoryDef(
    key="do.itbis.exempt", label="Exento", generic=GenericCategory.EXEMPT, rates=None, source=FORMAT_ECF
)
CATEGORIES: Final = (STANDARD, REDUCED, ZERO, EXEMPT)
