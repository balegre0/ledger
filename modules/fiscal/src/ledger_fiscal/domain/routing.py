"""Routing (§7.6): a commercial document sent to a fiscal channel, once per (document, channel).

An issued document goes to every active enrollment of its company and mode whose channel covers its kind
and direction (normally one): invoice → channel documents is 1:N keyed by channel. A ``Routing`` records
that the document was sent to ``channel`` for ``enrollment``; it is kept before the channel is asked to
number the document and never changes, so routing again (the recovery path, a retry) finds it and asks the
same channel for the same enrollment, which answers the document it numbered (Decision 35).
"""

from dataclasses import dataclass
from typing import final
lazy from datetime import datetime
lazy from uuid import UUID

lazy from ledger_fiscal.domain.enrollments import EnrollmentId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Routing:
    """A document routed to a channel for one of the company's enrollments (see the module)."""

    invoice: UUID
    """The commercial document's id (invoicing's)."""
    channel: str
    enrollment: EnrollmentId
    routed_at: datetime
