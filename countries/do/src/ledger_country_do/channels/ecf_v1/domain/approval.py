"""A company's commercial approval of an e-CF it received, sent out (spec §8.6.5 step 6; DGII's Paso 11).

The company (the e-CF's buyer) decides, once (``Decision``); its ACECF goes to two parties apart, the e-CF's
emitter (at the ``urlAceptacion`` DGII's directory lists for it) and DGII (``aprobacioncomercial``), and
where sending it to each stands is a ``Sending``:

- ``pending``: not sent yet, or to be tried again (the party out of reach);
- ``sent``: the party took it (the emitter's 200; DGII's answer, its words kept);
- ``refused``: the party refused it (the emitter's 400; DGII's refusal, its messages kept): never sent again;
- ``not_applicable`` (the emitter only): DGII's directory lists no receiver for it, or none in the
  environment (CerteCF);
- ``outcome_unknown`` (DGII only): it may be at DGII, its answer lost: **never sent again** (Review Focus
  1); a person settles it;
- ``failed``: out of reach for the seven days of Decision 21's schedule (``domain.delivery``) from the
  decision: a person looks at it.
"""

from enum import StrEnum
from typing import Final

from ledger_country_do.channels.ecf_v1.domain.exchanges import REJECTION_REASON

MAX_REASON: Final = REJECTION_REASON
"""The longest reason a rejection gives (the ACECF's ``DetalleMotivoRechazo``)."""


class Decision(StrEnum):
    """What the company decided of an e-CF it received."""

    APPROVED = "approved"
    REJECTED = "rejected"


class Decided(StrEnum):
    """Which received documents a list keeps, by what the company decided of them."""

    NONE = "none"
    """Not decided yet."""
    APPROVED = "approved"
    REJECTED = "rejected"


class Sending(StrEnum):
    """Where sending the company's ACECF to one party stands (see the module)."""

    PENDING = "pending"
    SENT = "sent"
    REFUSED = "refused"
    NOT_APPLICABLE = "not_applicable"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FAILED = "failed"

    @property
    def settled(self) -> bool:
        """Whether nothing is left to send to the party."""
        return self is not Sending.PENDING
