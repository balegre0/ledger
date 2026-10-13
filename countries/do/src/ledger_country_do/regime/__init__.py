"""Regime ``do``: the Dominican Republic's tax law as typed, effective-dated data (spec §8.2, §8.6.2).

Standard library only (import-linter: "The DO regime is pure data"). ``REGIME`` is what the entry point
``ledger.countries.regimes`` will name (``ledger_country_do.regime:REGIME``, spec §4.3) once the runtime loads
regimes.
"""

from types import MappingProxyType
from typing import Final

from ledger_country_do.regime.definitions import RegimeDef
from ledger_country_do.regime.document_types import TYPES
from ledger_country_do.regime.parameters import PARAMETERS
from ledger_country_do.regime.rounding import ROUNDING
from ledger_country_do.regime.sources import ALL
from ledger_country_do.regime.tax_ids import SCHEMES
from ledger_country_do.regime.taxes import CATEGORIES

REGIME: Final = RegimeDef(
    key="do",
    country="DO",
    currency="DOP",
    time_zone="America/Santo_Domingo",
    labels=MappingProxyType({"es": "República Dominicana", "en": "Dominican Republic"}),
    tax_id_schemes=SCHEMES,
    tax_categories=CATEGORIES,
    document_types=TYPES,
    rounding=ROUNDING,
    parameters=PARAMETERS,
    sources=ALL,
)
