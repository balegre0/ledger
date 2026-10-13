"""Where regime ``do``'s values come from: one ``Source`` per document, cited by the values that use it."""

from datetime import date
from typing import Final

from ledger_country_do.regime.definitions import Source

RETRIEVED: Final = date(2026, 10, 13)
COVERED_FROM: Final = date(2019, 1, 1)
"""The first day the pack asserts values for: channel do-ecf-v1's ``valid_from`` (spec §4.3)."""

STDNUM_RNC: Final = Source(
    title="python-stdnum, stdnum.do.rnc: the RNC's check digit (weights 7 9 8 6 5 4 3 2, modulo 11)",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/rnc.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
STDNUM_CEDULA: Final = Source(
    title="python-stdnum, stdnum.do.cedula: the cédula's check digit (Luhn) and DGII's numbers that fail it",
    url="https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/do/cedula.py",
    version="master as read on 2026-10-13; DGII publishes no algorithm (secondary source)",
    retrieved_on=RETRIEVED,
)
