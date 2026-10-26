"""An ACECF to the emitter's approval service (DT-EMI ``/fe/aprobacioncomercial/api/ecf``): what it answered.

``BuyerClient.send_acecf`` posts it under delivery's rules (the directory's ``urlAceptacion`` checked like any
counterparty's endpoint, authentication only when switched on): 200 is ``ApprovalSent``, 400 the emitter's
refusal (``ApprovalRefused``: not retried); anything else is ``BuyerUnavailableError`` (retried later).
"""

from dataclasses import dataclass
from typing import Final, final
lazy from datetime import datetime

lazy from ledger_country_do.channels.ecf_v1.application.ports import SeedSigned

APPROVAL_PATH: Final = "/fe/aprobacioncomercial/api/ecf"
ACECF_FILE: Final = "ACECF.xml"
"""What an ACECF sent is named: DT-EMI names no file for it (checked against the simulator)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalSent:
    """The emitter took the approval (200) at ``sent_at``."""

    sent_at: datetime
    seeds: tuple[SeedSigned, ...]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalRefused:
    """The emitter refused the approval (400): sending it again would be refused again."""

    seeds: tuple[SeedSigned, ...]
