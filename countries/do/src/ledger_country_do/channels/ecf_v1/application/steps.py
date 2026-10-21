"""What a ``do_ecf.advance`` run answers: wait for a reason, run again at a moment, or nothing left to do.

The job's adapter turns each into the platform's outcome (``Park``, ``RunAgain``, ``Done``); the use cases
import no platform. A ``Wait`` names what wakes it (``ParkReason``): ``Channel.resume`` and the range
registration wake the jobs waiting for their reason.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import final
lazy from datetime import datetime


class ParkReason(StrEnum):
    """Why the job waits, and what wakes it."""

    CERTIFICATE = "certificate"
    """The company cannot sign (no certificate, one not valid, the signer's clock): a certificate installed,
    or an operator once the clock is fixed (``Channel.resume``), wakes it."""
    BLOCKED = "blocked"
    """It needs a person: an operator unblocks it."""
    OUTCOME_UNKNOWN = "outcome_unknown"
    """DGII listed no track id of it within the window, or cannot (CerteCF): an operator (Owner item 3)."""
    ANSWER_MISMATCH = "answer_mismatch"
    """DGII answered about another issuer or e-NCF: a person looks before anything else is asked."""
    RANGE_MISSING = "range_missing"
    """No number to give: a range registered wakes it (Decision 6; no advance job waits for it yet)."""


@final
@dataclass(frozen=True, slots=True)
class Wait:
    """Wait until something outside wakes the job (``reason``)."""

    reason: ParkReason


@final
@dataclass(frozen=True, slots=True)
class Again:
    """Run the job again at ``at`` (the database's time): the next step, or the same one later."""

    at: datetime


@final
@dataclass(frozen=True, slots=True)
class Finished:
    """Nothing left for the job: DGII decided, or the document was voided."""


type Next = Wait | Again | Finished
"""What a run answers."""
