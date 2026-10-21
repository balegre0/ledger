"""Regime ``do``: the Dominican Republic's tax law as typed, effective-dated data (spec §8.2, §8.6.2).

Built of the kernel's regime types and nothing else (import-linter: "Regimes are pure"). ``REGIME`` is what
the entry point ``ledger.countries.regimes: do`` names (``ledger_country_do.regime:REGIME``, ADR 0005): the
runtime's ``Regimes`` loads it, and every module reads tax ids with its schemes.
"""

from typing import Final

from ledger_country_do.regime.document_types import TYPES
from ledger_country_do.regime.fields import FIELDS
from ledger_country_do.regime.parameters import PARAMETERS
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do.regime.sources import ALL
from ledger_country_do.regime.tax_ids import SCHEMES
from ledger_country_do.regime.taxes import CATEGORIES
from ledger_kernel.jurisdiction import CountryCode, Jurisdiction
from ledger_kernel.money import Currency
from ledger_kernel.regimes import Locale, RegimeDef, RegimeKey

REGIME: Final = RegimeDef(
    key=RegimeKey("do"),
    jurisdiction=Jurisdiction(CountryCode("DO")),
    currency=Currency.of("DOP"),
    time_zone="America/Santo_Domingo",
    labels={Locale("es"): "República Dominicana", Locale("en"): "Dominican Republic"},
    tax_id_schemes=SCHEMES,
    tax_categories=CATEGORIES,
    document_types=TYPES,
    rounding=ROUNDING,
    fields=FIELDS,
    parameters=PARAMETERS,
    sources=ALL,
)
