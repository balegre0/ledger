"""e-CF reception (spec §3.8; Decisions 15-17): what other emitters send a company, answered with a receipt.

A company receives in one environment (the receiver's path names it; its mode is the one the environment
serves) from a sender its receiver authenticated: the token names the RNCs and cédulas the sender's
certificate names, or a DGII stand-in (Decision 17), which sends for anyone in a test environment.

Each document is kept raw in the archive **first**, whatever it is. Then it is inspected (``InboundFormat``):
read (hardened, its schema and values), its signature verified and its certificate trusted for its issuer, in
that order. Whatever the receipt would name as the issuer must be the sender's (``NotTheSenderError``: a token
never gets a receipt about another issuer's document). The answer is an ARECF the receiving company signs
(``Signatures``) and the archive keeps:

- not received (``Estado`` 1) for DGII's reason: **1** specification, when the document cannot be read whole
  (the receipt names what it names, read leniently; one that names nothing a receipt can hold is
  ``NoReceiptError``: no receipt, a 400); **2** signature, when its signature does not hold or its
  certificate is not trusted for its issuer; **4** not this receiver, when its buyer is another taxpayer or
  it names none (no electronic receiver receives a 43, a 47 or a 32 without a buyer's RNC); **3**
  duplicate, when the issuer sent the same e-NCF before with other bytes;
- received (``Estado`` 0) once the document is recorded (``ReceivedDocuments``) and announced, in one unit.

The same bytes again are answered with the receipt they were given (read back from the archive). A receipt
names the receiving company as its buyer, always: it never signs a statement about another taxpayer.
Nothing but the raw copy is kept of a document before its signature is trusted.

A reception answers its receipt with how it went (``Answered.outcome``), which the receiver's route reports
(``ReceptionReport``) with the receiving company and the environment, never the document, its parties or its
sender; so does a reception refused without a receipt (``NoReceiptError``, ``NotTheSenderError``). A
reception that fails (the archive or the signer out of reach) reports nothing: its error does.
"""

import asyncio
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, assert_type, final
lazy from collections.abc import Callable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.application.received import ReceivedDocument, ReceivedExistsError
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError, EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundNames, NotReceivedReason, Receipt
from ledger_kernel.errors import UnauthenticatedError
from ledger_kernel.ids import new_id
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archived, Signatures, SigningAs
lazy from ledger_country_do.channels.ecf_v1.application.ranges import NumberingUnits
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import CommercialApproval, InboundEcf
lazy from ledger_kernel.tenancy import Tenant


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Sender:
    """Whom a receiver's token was issued to: the RNCs and cédulas its certificate names, or a stand-in."""

    identities: frozenset[str]
    stand_in: bool
    """A DGII stand-in (Decision 17): it sends for any issuer, in a test environment only."""

    def sends_for(self, party: str) -> bool:
        """Whether the sender may send a document ``party`` signs (an issuer's e-CF, a buyer's approval)."""
        return self.stand_in or party in self.identities


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Receiver:
    """A company receiving in an environment: whom its receipts are signed as, and its tax id there."""

    who: SigningAs
    rnc: str
    """The tax id the receiver's path names, whose company ``who`` is: what its receipts name as the buyer."""
    environment: Environment


class ReceptionOutcome(StrEnum):
    """How a reception was answered: received, the same bytes again, a reason not to, or no receipt at all."""

    RECEIVED = "received"
    AGAIN = "again"
    """The same bytes again: answered with the receipt they were given."""
    SPECIFICATION = "specification"
    SIGNATURE = "signature"
    DUPLICATE = "duplicate"
    NOT_THIS_RECEIVER = "not_this_receiver"
    NO_RECEIPT = "no_receipt"
    """Nothing a receipt can name (``NoReceiptError``, a 400)."""
    NOT_THE_SENDER = "not_the_sender"
    """A document of another issuer than the token's sender (``NotTheSenderError``, a 401)."""


_NOT_RECEIVED: Final = MappingProxyType(
    {
        NotReceivedReason.SPECIFICATION: ReceptionOutcome.SPECIFICATION,
        NotReceivedReason.SIGNATURE: ReceptionOutcome.SIGNATURE,
        NotReceivedReason.DUPLICATE: ReceptionOutcome.DUPLICATE,
        NotReceivedReason.NOT_THIS_RECEIVER: ReceptionOutcome.NOT_THIS_RECEIVER,
    }
)


@final
@dataclass(frozen=True, slots=True)
class Answered:
    """A reception's signed receipt, and how it answered the document."""

    receipt: bytes
    outcome: ReceptionOutcome


class ReceptionReport(Protocol, metaclass=ABCMeta):
    """What a reception tells operations (see the module): never a tax id, an e-NCF or a document."""

    @abstractmethod
    def answered(self, receiver: Receiver, outcome: ReceptionOutcome) -> None:
        """A reception at ``receiver`` was answered ``outcome``."""


@final
@dataclass(frozen=True, slots=True)
class Trusted:
    """A document read whole, its signature holding under a certificate trusted for its issuer."""

    ecf: InboundEcf


@final
@dataclass(frozen=True, slots=True)
class Untrusted:
    """A document read whole whose signature does not hold, or whose certificate is not trusted for it."""

    ecf: InboundEcf


@final
@dataclass(frozen=True, slots=True)
class Unreadable:
    """A document that cannot be read whole: what a receipt of it may name, if anything."""

    names: InboundNames | None


type Inspection = Trusted | Untrusted | Unreadable


class InboundFormat(Protocol, metaclass=ABCMeta):
    """What arrives, read and verified (DGII's formats, the verifier, the trust policy), and receipts written.

    ``environment``: where it arrived (a stand-in is honoured outside the live one only); ``received_at``:
    when (a certificate must be valid then, and when the document says it was signed).
    """

    @abstractmethod
    def ecf(self, document: bytes, *, environment: Environment, received_at: datetime) -> Inspection:
        """``document``, an e-CF, read, its signature verified and trusted for its ``RNCEmisor``."""

    @abstractmethod
    def approval(
        self, document: bytes, *, environment: Environment, received_at: datetime
    ) -> CommercialApproval | None:
        """``document``, an ACECF, read, verified and trusted for its ``RNCComprador``; ``None`` if not."""

    @abstractmethod
    def receipt(self, receipt: Receipt) -> bytes:
        """``receipt``'s ARECF, unsigned, valid for its schema."""


class ReceptionArchive(Protocol, metaclass=ABCMeta):
    """The legal archive (``Archive``'s rules) for what the receiver takes in and answers.

    Each method raises ``ArchiveUnavailableError`` when the archive cannot be reached.
    """

    @abstractmethod
    async def received(self, tenant: Tenant, document: bytes) -> Archived:
        """``document``, an e-CF sent to ``tenant``, kept as it arrived, whatever it is."""

    @abstractmethod
    async def receipt(self, tenant: Tenant, signed: bytes) -> Archived:
        """``signed``, an ARECF ``tenant`` answered, kept."""

    @abstractmethod
    async def approval(self, tenant: Tenant, document: bytes) -> Archived:
        """``document``, an ACECF sent to ``tenant``, kept as it arrived, whatever it is."""

    @abstractmethod
    async def read(self, archived: Archived) -> bytes:
        """The bytes kept as ``archived``, checked against its SHA-256 (``Archive.read``)."""


class NoReceiptError(EcfInvalidError):
    """A document no receipt can name (no issuer, buyer or e-NCF that one can hold): answered without one."""

    code: ClassVar[str] = "do_ecf_reception_unnamed"

    def __init__(self) -> None:
        super().__init__("the document names nothing a receipt can name")


class NotTheSenderError(EcfError, UnauthenticatedError):
    """A document of another party than the token's sender: its token does not speak for that party."""

    code: ClassVar[str] = "do_ecf_receiver_not_the_sender"

    def __init__(self) -> None:
        super().__init__("the token's sender does not send for the party the document names")


@final
class Receiving:
    """e-CF reception (see the module)."""

    __slots__ = ("_archive", "_clock", "_inbound", "_signatures", "_units")

    def __init__(
        self,
        units: NumberingUnits,
        inbound: InboundFormat,
        archive: ReceptionArchive,
        signatures: Signatures,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._units = units
        self._inbound = inbound
        self._archive = archive
        self._signatures = signatures
        self._clock = clock

    async def receive(self, receiver: Receiver, sender: Sender, document: bytes) -> Answered:
        """The signed receipt of ``document``, sent to ``receiver`` by ``sender``, and how it answered it."""
        tenant = receiver.who.tenant
        kept = await self._archive.received(tenant, document)
        now = self._clock()
        inspection = await asyncio.to_thread(  # CPU work on up to 2 MiB: off the event loop
            self._inbound.ecf, document, environment=receiver.environment, received_at=now
        )
        match inspection:
            case Unreadable(names=InboundNames() as names):
                return await self._refused(
                    receiver, sender, names.issuer_rnc, names.encf, NotReceivedReason.SPECIFICATION
                )
            case Unreadable():
                raise NoReceiptError
            case Untrusted(ecf=ecf):
                return await self._refused(
                    receiver, sender, ecf.issuer_rnc, ecf.encf, NotReceivedReason.SIGNATURE
                )
            case _:
                assert_type(inspection, Trusted)
                return await self._trusted(receiver, sender, inspection.ecf, kept)

    async def _trusted(self, receiver: Receiver, sender: Sender, ecf: InboundEcf, kept: Archived) -> Answered:
        if not sender.sends_for(ecf.issuer_rnc):
            raise NotTheSenderError
        if ecf.buyer_rnc != receiver.rnc:  # another buyer's, or a document naming none (a 32, 43, 47)
            return await self._not_received(
                receiver, ecf.issuer_rnc, ecf.encf, NotReceivedReason.NOT_THIS_RECEIVER
            )
        earlier = await self._earlier(receiver, ecf)
        if earlier is not None:
            return await self._again(receiver, ecf, kept, earlier)
        receipt = await self._answered(receiver, ecf.issuer_rnc, ecf.encf, None)
        signed = await self._archive.receipt(receiver.who.tenant, receipt)
        record = ReceivedDocument(
            id=new_id(),
            environment=receiver.environment,
            ecf=ecf,
            received_at=self._clock(),
            document=kept,
            receipt=signed,
            version=1,
        )
        try:
            async with self._units.unit(receiver.who.tenant) as unit:
                await unit.received.add(record)
                unit.announcements.received(record)
        except ReceivedExistsError:  # recorded by a reception that raced this one: answered as again
            return await self._again(receiver, ecf, kept, await self._earlier(receiver, ecf))
        return Answered(receipt, ReceptionOutcome.RECEIVED)

    async def _earlier(self, receiver: Receiver, ecf: InboundEcf) -> ReceivedDocument | None:
        async with self._units.unit(receiver.who.tenant) as unit:
            return await unit.received.find(receiver.environment, ecf.issuer_rnc, ecf.encf)

    async def _again(
        self, receiver: Receiver, ecf: InboundEcf, kept: Archived, earlier: ReceivedDocument | None
    ) -> Answered:
        """The receipt of the same bytes again, or a duplicate's of other bytes (Decision 16)."""
        if earlier is not None and earlier.document.sha256 == kept.sha256:
            return Answered(await self._archive.read(earlier.receipt), ReceptionOutcome.AGAIN)
        return await self._not_received(receiver, ecf.issuer_rnc, ecf.encf, NotReceivedReason.DUPLICATE)

    async def _refused(
        self, receiver: Receiver, sender: Sender, issuer: str, encf: Encf, reason: NotReceivedReason
    ) -> Answered:
        if not sender.sends_for(issuer):
            raise NotTheSenderError
        return await self._not_received(receiver, issuer, encf, reason)

    async def _not_received(
        self, receiver: Receiver, issuer: str, encf: Encf, reason: NotReceivedReason
    ) -> Answered:
        return Answered(await self._answered(receiver, issuer, encf, reason), _NOT_RECEIVED[reason])

    async def _answered(
        self, receiver: Receiver, issuer: str, encf: Encf, reason: NotReceivedReason | None
    ) -> bytes:
        """The receipt of ``encf``, received when ``reason`` is ``None``: signed, and kept unless received.

        A receipt of a document received is kept by the caller with the document's record.
        """
        at = self._clock()
        receipt = Receipt(
            issuer_rnc=issuer,
            buyer_rnc=receiver.rnc,
            encf=encf,
            received=reason is None,
            reason=reason,
            at=at,
        )
        signed = await self._signatures.document(
            receiver.who, self._inbound.receipt(receipt), signed_at=at, reference=str(encf)
        )
        if reason is not None:
            _ = await self._archive.receipt(receiver.who.tenant, signed.document)
        return signed.document
