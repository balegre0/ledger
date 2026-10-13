"""DGII's environments and hosts (DT-DGII): ``testecf`` (pre-certification), ``certecf``, ``ecf`` (live).

Test mode uses ``testecf`` and ``certecf``; only ``ecf`` is live (spec §4.3 ``live_environments``), and the
spike never reaches it (Global Constraints). Paths are case-insensitive at DGII; the lower-case spelling of
DT-DGII's URL lists is used everywhere.
"""

from enum import StrEnum
from typing import Final

OFFICIAL_ECF_HOST: Final = "https://ecf.dgii.gov.do"
OFFICIAL_FC_HOST: Final = "https://fc.dgii.gov.do"
"""The host of the consumer-invoice summary (RFCE) services."""


class Environment(StrEnum):
    """A DGII environment, by its URL segment."""

    TESTECF = "testecf"
    CERTECF = "certecf"
    ECF = "ecf"

    @property
    def live(self) -> bool:
        """Whether documents sent there are legally issued."""
        return self is Environment.ECF
