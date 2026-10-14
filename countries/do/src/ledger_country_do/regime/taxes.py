"""ITBIS, the Dominican VAT: its categories and their dated rates (FMT, Totales: tasas 18 %, 16 %, 0 %).

The timelines start on ``COVERED_FROM`` (2019-01-01, do-ecf-v1's ``valid_from``): every e-CF falls after it,
and the rates' earlier history (18 % from Ley 253-12; 16 % reached in phases from 2013 to 2016) is not
asserted. A rate change is a new ``Since`` at the head of a timeline, never an edit of an old one. An exempt
line is charged nothing: its category's rate is 0 % and its kind says why (``EXEMPT``, not ``ZERO``).
"""

from decimal import Decimal
from typing import Final

from ledger_country_do.regime.sources import COVERED_FROM, FORMAT_ECF
from ledger_kernel.dated import Since, Timeline
from ledger_kernel.regimes import Locale, RateValue, TaxCategoryDef, TaxCategoryKey, TaxCategoryKind


def _category(key: str, label: str, kind: TaxCategoryKind, percent: int) -> TaxCategoryDef:
    return TaxCategoryDef(
        key=TaxCategoryKey(key),
        labels={Locale("es"): label},
        kind=kind,
        rates=Timeline(Since(COVERED_FROM, RateValue(percent=Decimal(percent), source=FORMAT_ECF))),
    )


STANDARD: Final = _category("do.itbis.standard", "ITBIS 18 %", TaxCategoryKind.STANDARD, 18)
REDUCED: Final = _category("do.itbis.reduced", "ITBIS 16 %", TaxCategoryKind.REDUCED, 16)
ZERO: Final = _category("do.itbis.zero", "ITBIS 0 %", TaxCategoryKind.ZERO, 0)
EXEMPT: Final = _category("do.itbis.exempt", "Exento", TaxCategoryKind.EXEMPT, 0)
CATEGORIES: Final = (STANDARD, REDUCED, ZERO, EXEMPT)
