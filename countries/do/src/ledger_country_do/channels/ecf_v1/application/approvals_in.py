"""Commercial approvals received (spec §3.8, §8.6.5 step 5; DGII's Paso 11): a buyer's word on our e-CF.

A buyer the receiver authenticated sends its ACECF about one of the company's documents. It is kept raw in
the archive **first**; then read, its signature verified and its certificate trusted for its
``RNCComprador`` (``InboundFormat.approval``), who must be the token's sender (``NotTheSenderError``).
It must name the company as the issuer (the receiver's path) and a document the company issued in the
receiver's environment that DGII accepted (or accepted with observations: DGII takes approvals only of
accepted e-CF), with that document's date and total. Then the document keeps it (``commercial_approval``, the
last word the buyer sent) at a new version, announced (``fiscal_channel.document.updated``), in one unit; the
same word again changes nothing; its moment is kept in UTC. Anything else is refused
(``ApprovalRefusedError``: a bare 400).
"""

from dataclasses import replace
from datetime import UTC
from typing import ClassVar, Final, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.application.documents import Approval
from ledger_country_do.channels.ecf_v1.application.receiving import NotTheSenderError
from ledger_country_do.channels.ecf_v1.domain.ecf import EcfDocumentState
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
lazy from ledger_country_do.channels.ecf_v1.application.issuing import Issuing
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.application.receiving import (
    InboundFormat,
    Receiver,
    ReceptionArchive,
    Sender,
)

APPROVABLE: Final = frozenset({EcfDocumentState.ACCEPTED, EcfDocumentState.ACCEPTED_CONDITIONALLY})
"""The states of a document its buyer may approve or reject: DGII accepted it."""


class ApprovalRefusedError(EcfInvalidError):
    """A commercial approval the company does not take (see the module); it says nothing of which check."""

    code: ClassVar[str] = "do_ecf_approval_refused"

    def __init__(self) -> None:
        super().__init__("the commercial approval is refused")


@final
class Approving:
    """Commercial approvals received (see the module)."""

    __slots__ = ("_archive", "_clock", "_inbound", "_issuing", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        inbound: InboundFormat,
        archive: ReceptionArchive,
        issuing: Issuing,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._units = units
        self._inbound = inbound
        self._archive = archive
        self._issuing = issuing
        self._clock = clock

    async def receive(self, receiver: Receiver, sender: Sender, document: bytes) -> None:
        """Record ``document``, an ACECF ``sender`` sent ``receiver``, on the document it approves."""
        tenant = receiver.who.tenant
        _ = await self._archive.approval(tenant, document)
        approval = self._inbound.approval(
            document, environment=receiver.environment, received_at=self._clock()
        )
        if approval is None:
            raise ApprovalRefusedError
        if not sender.sends_for(approval.buyer_rnc):
            raise NotTheSenderError
        if approval.issuer_rnc != receiver.rnc:
            raise ApprovalRefusedError
        async with self._units.unit(tenant) as unit:
            ours = await unit.documents.by_number(receiver.environment, approval.encf)
            if (
                ours is None
                or ours.state not in APPROVABLE
                or ours.issued_on != approval.issued_on
                or await self._issuing.total(unit, ours) != approval.total
            ):
                raise ApprovalRefusedError
            word = Approval(
                approved=approval.approved, reason=approval.reason, at=approval.at.astimezone(UTC)
            )
            if ours.commercial_approval == word:
                return
            approved = replace(ours, commercial_approval=word, version=ours.version + 1, updated_at=unit.now)
            await unit.documents.changed(approved)
            unit.announcements.document_updated(approved)
