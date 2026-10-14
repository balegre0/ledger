"""Regime ``do``'s dated parameters, each with its source (spec §8.6.2)."""

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final
lazy from collections.abc import Mapping

from ledger_country_do.regime.sources import COVERED_FROM, DECREE_587_24, FORMAT_ECF, SERVICES_DGII
from ledger_kernel.dated import Since, Timeline
lazy from ledger_kernel.regimes import Source

type ParameterValue = Decimal | int | str
"""What a dated parameter of a regime holds (the kernel's ``RegimeDef.parameters``)."""

RFCE_THRESHOLD: Final = "rfce_threshold"
"""A type-32 invoice whose total is below it is sent as a summary (RFCE); from it, in full (DT-DGII)."""
CONTINGENCY_CONNECTIVITY_HOURS: Final = "contingency_connectivity_hours"
"""Decreto 587-24 Art. 40.1: documents signed offline are sent within 72 h of the connection's return."""
CONTINGENCY_PAPER_MAX_DAYS: Final = "contingency_paper_max_days"
"""Decreto 587-24 Arts. 40.2-42: a contingency with paper NCF lasts at most 15 calendar days."""
CONTINGENCY_REPLACEMENT_DAYS: Final = "contingency_replacement_days"
"""Decreto 587-24 Art. 42: replacement e-CF go to DGII within 30 calendar days after the contingency."""
LEGAL_NAME_MAX: Final = "legal_name_max"
LINES_MAX: Final = "lines_max"
RFCE_LINES_MAX: Final = "rfce_lines_max"
"""[06] §2: an RFCE-summarized type 32 may hold 10,000 lines, but the e-CF 32 schema allows 1,000 (``Item``
at most 1,000 times) and the full e-CF is always built. Kept as data and read by nothing until DGII settles
which limit holds (the plan's open fact F7)."""
_DECREE_DATE: Final = date(2024, 10, 10)


def _since(day: date, value: ParameterValue) -> Timeline[ParameterValue]:
    return Timeline(Since(day, value))


PARAMETERS: Final[Mapping[str, Timeline[ParameterValue]]] = MappingProxyType(
    {
        RFCE_THRESHOLD: _since(COVERED_FROM, Decimal("250000.00")),
        CONTINGENCY_CONNECTIVITY_HOURS: _since(_DECREE_DATE, 72),
        CONTINGENCY_PAPER_MAX_DAYS: _since(_DECREE_DATE, 15),
        CONTINGENCY_REPLACEMENT_DAYS: _since(_DECREE_DATE, 30),
        LEGAL_NAME_MAX: _since(COVERED_FROM, 150),
        LINES_MAX: _since(COVERED_FROM, 1000),
        RFCE_LINES_MAX: _since(COVERED_FROM, 10000),
    }
)
PARAMETER_SOURCES: Final[Mapping[str, Source]] = MappingProxyType(
    {
        RFCE_THRESHOLD: SERVICES_DGII,
        CONTINGENCY_CONNECTIVITY_HOURS: DECREE_587_24,
        CONTINGENCY_PAPER_MAX_DAYS: DECREE_587_24,
        CONTINGENCY_REPLACEMENT_DAYS: DECREE_587_24,
        LEGAL_NAME_MAX: FORMAT_ECF,
        LINES_MAX: FORMAT_ECF,
        RFCE_LINES_MAX: SERVICES_DGII,
    }
)
