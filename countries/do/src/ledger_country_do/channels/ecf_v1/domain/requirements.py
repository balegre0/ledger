"""What do-ecf-v1 requires of a company before its enrollment in an environment can issue (Decision 3).

In the channel's words, in this order:

- ``certificate``: the issuing company has no certificate whose validity covers now (the signer's answer);
- ``numbering_range``: no range of a consumer or credit invoice (31, 32) in the enrollment's environment can
  number a document today, in Santo Domingo (active, numbers left, not expired); a note's range numbers none;
- ``receiver_url``: the receiver's public URL is not configured, so the company cannot register the URLs
  DGII and its buyers send to (Decision 15).

Each is read when asked: an enrollment's review asks again (Decision 31).
"""

from enum import StrEnum
from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.domain.encf import EcfType
from ledger_country_do.channels.ecf_v1.domain.notation import local_date
lazy from ledger_country_do.channels.ecf_v1.domain.numbering import RangeState


class Requirement(StrEnum):
    """Something the company must still provide, in the channel's word for it."""

    CERTIFICATE = "certificate"
    NUMBERING_RANGE = "numbering_range"
    RECEIVER_URL = "receiver_url"


NUMBERING_TYPES: Final = frozenset({EcfType.CREDIT_INVOICE, EcfType.CONSUMER_INVOICE})
"""The types a company issues first: a range of one of them makes an environment ready to number."""


def due(
    *, certificate: bool, ranges: Sequence[RangeState], now: datetime, receiver: bool
) -> tuple[Requirement, ...]:
    """What is still due, in ``Requirement``'s order.

    ``certificate``: whether a certificate covers ``now``; ``ranges``: the enrollment environment's ranges;
    ``receiver``: whether the receiver's public URL is configured.
    """
    today = local_date(now)
    numbers = any(
        each.ecf_type in NUMBERING_TYPES and each.usable_on(today) and each.remaining > 0 for each in ranges
    )
    missing = {
        Requirement.CERTIFICATE: not certificate,
        Requirement.NUMBERING_RANGE: not numbers,
        Requirement.RECEIVER_URL: not receiver,
    }
    return tuple(requirement for requirement in Requirement if missing[requirement])
