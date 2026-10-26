"""A company's e-CF documents as the use cases see them, and the ports that keep and move them (§8.6.4).

An ``EcfDocument`` is the e-CF of one invoice in one mode (Decision 35): its number, the range it came from
(and that range's last day, read with it), where it stands, the route DGII receives it by, and once signed,
its ``Signature``: when, its security code and QR, and where the archive keeps what was signed. It keeps the
``FiscalDraft`` it was numbered for, so every signature signs what the request checked.

Once signed, it is submitted once (its mark, ``submission_started_at``, committed first) and followed until
DGII decides: its track id, when its outcome became unknown, how often DGII was asked, DGII's last code,
``secuenciaUtilizada``, messages and what became of its number. Once accepted, it is delivered to its buyer
(``delivery``): since when, how many attempts failed, why it failed or did not apply, and where the archive
keeps the buyer's receipt.

``Documents`` are the unit's company's documents in the unit's mode; ``Advances`` the unit's queue of the
``do_ecf.advance`` job, which takes each document on from where it stands; ``Submissions`` the track ids
each document holds, which no document with the same e-NCF ever adopts; ``Deliveries`` the unit's queue of
the ``do_ecf.deliver`` job; ``CompanyOptions`` how the company delivers to its buyers.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, final
lazy from collections.abc import Collection, Mapping
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError, EcfError
from ledger_country_do.channels.ecf_v1.domain.routing import Route
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archived
lazy from ledger_country_do.channels.ecf_v1.application.steps import ParkReason
lazy from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import NumberFate
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft
lazy from ledger_fiscal_kit.status import AuthorityMessage


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signature:
    """A document's signature as recorded: when, what it prints, how, where the archive keeps it."""

    signed_at: datetime
    security_code: str
    qr_url: str
    emission_mode: EmissionMode
    ecf: Archived
    rfce: Archived | None
    """The summary, on the RFCE route; ``None`` on the reception route."""

    def sent(self, route: Route) -> Archived:
        """What is sent to DGII on ``route``: the summary on the RFCE route, else the e-CF."""
        if route is not Route.RFCE:
            return self.ecf
        if self.rfce is None:
            raise DocumentIncoherentError(Missing.SUMMARY)
        return self.rfce


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EcfDocument:
    """An invoice's e-CF (see the module)."""

    id: UUID
    invoice: DocumentRef
    environment: Environment
    encf: Encf
    range_id: UUID
    range_expires_on: date | None
    """The last day of the range of its number (Santo Domingo's date); ``None`` if DGII dated it not."""
    state: EcfDocumentState
    route: Route
    delivery: DeliveryState
    issued_on: date
    signature: Signature | None
    draft: FiscalDraft
    version: int
    """From 1, one more at each change: what ``fiscal_channel.document.updated`` carries."""
    created_at: datetime
    updated_at: datetime
    submission_started_at: datetime | None = None
    """The submission's mark, committed before the one call that sends it: once set, never sent again."""
    track_id: str | None = None
    """The track id DGII gave it, or one adopted after an unknown outcome."""
    track_id_since: datetime | None = None
    """When its outcome first became unknown: the track-id window runs from it, never reset."""
    polls: int = 0
    """How often DGII was asked about it, or could not be reached: the next wait grows with it."""
    outcome_code: int | None = None
    """DGII's last code about it, as the service that answered reads it."""
    sequence_used: bool | None = None
    """DGII's ``secuenciaUtilizada``, once it said."""
    number_fate: NumberFate | None = None
    """What became of its number once DGII decided."""
    messages: tuple[AuthorityMessage, ...] = ()
    """DGII's messages about it, verbatim within the kit's bounds."""
    delivery_since: datetime | None = None
    """When its delivery to the buyer started (its first run, or the client's retry): tried for 7 days."""
    delivery_attempts: int = 0
    """How many attempts at delivering it failed since then: the next wait grows with them."""
    delivery_reason: str | None = None
    """Why its delivery failed, failed last, or does not apply: a code, never a tax id or a document."""
    receipt: Archived | None = None
    """Where the archive keeps the buyer's receipt (ARECF): a trusted one, or one refused (as evidence)."""

    def signed(self) -> Signature:
        """Its signature: a document past ``numbered`` has one (``DocumentIncoherentError`` otherwise)."""
        if self.signature is None:
            raise DocumentIncoherentError(Missing.SIGNATURE)
        return self.signature

    def tracked(self) -> str:
        """Its track id: a document DGII deliberates about has one (``DocumentIncoherentError`` otherwise)."""
        if self.track_id is None:
            raise DocumentIncoherentError(Missing.TRACK_ID)
        return self.track_id

    def unknown_since(self) -> datetime:
        """When its outcome first became unknown (``DocumentIncoherentError`` if it never did)."""
        if self.track_id_since is None:
            raise DocumentIncoherentError(Missing.UNKNOWN_SINCE)
        return self.track_id_since


class DocumentIncoherentError(EcfError):
    """A document lacks what its state requires (the table's checks forbid it): a defect, never retried."""

    code: ClassVar[str] = "do_ecf_document_incoherent"

    def __init__(self, missing: Missing) -> None:
        super().__init__(f"an e-CF document lacks {missing.value}")


class Missing(StrEnum):
    """What a document lacks (``DocumentIncoherentError``)."""

    SIGNATURE = "a signature"
    SUMMARY = "a summary"
    TRACK_ID = "a track id"
    UNKNOWN_SINCE = "the moment its outcome became unknown"


class DocumentExistsError(EcfConflictError):
    """The invoice got its document in another unit first (Decision 35): this one rolls back and reads it."""

    code: ClassVar[str] = "do_ecf_document_exists"

    def __init__(self) -> None:
        super().__init__("the invoice already has its e-CF document")


class Documents(Protocol, metaclass=ABCMeta):
    """The documents of the unit's company in the unit's mode."""

    @abstractmethod
    async def of_invoice(self, invoice: DocumentRef) -> EcfDocument | None:
        """The document of ``invoice``, or ``None`` (it was never issued here)."""

    @abstractmethod
    async def get(self, document: UUID, *, lock: bool) -> EcfDocument | None:
        """The document, or ``None``; with ``lock``, its row locked until the unit ends (``FOR UPDATE``)."""

    @abstractmethod
    async def add(self, document: EcfDocument) -> None:
        """Keep a new document, numbered in this unit.

        ``DocumentExistsError`` when its invoice has one already (kept by a unit that committed meanwhile):
        the unit is then to be rolled back.
        """

    @abstractmethod
    async def changed(self, document: EcfDocument) -> None:
        """Keep ``document``'s new state, signature, progress (mark, track id, DGII's answer) and version.

        Its row is locked in this unit.
        """

    @abstractmethod
    async def accepted(self, invoices: Collection[DocumentRef]) -> Mapping[DocumentRef, AcceptedEcf]:
        """The e-CF DGII accepted (with or without observations) of those of ``invoices`` that have one."""


class Advances(Protocol, metaclass=ABCMeta):
    """The unit's queue of the ``do_ecf.advance`` job."""

    @abstractmethod
    def enqueue(self, document: UUID, *, at: datetime) -> None:
        """Take ``document`` on from ``at``: once the unit commits, and once per document."""

    @abstractmethod
    def wake(self, reason: ParkReason) -> None:
        """Once the unit commits, take on every document of its company and mode waiting for ``reason``."""


class Submissions(Protocol, metaclass=ABCMeta):
    """The track ids the unit's company's documents were given or adopted, by document and by number."""

    @abstractmethod
    async def add(
        self, document: EcfDocument, track_id: str, *, at: datetime, received_at: datetime | None
    ) -> None:
        """Record that ``document`` holds ``track_id``; a track id it holds already is kept as it was.

        ``at``: when it is recorded; ``received_at``: when DGII says it received the document, if it said.
        """

    @abstractmethod
    async def excluded(self, document: EcfDocument) -> frozenset[str]:
        """The track ids recorded for other documents with ``document``'s e-NCF (Decision 9)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryOptions:
    """How a company delivers to its buyers, in a mode: whether it signs their seeds (off by default)."""

    buyer_authentication: bool = False


class CompanyOptions(Protocol, metaclass=ABCMeta):
    """The options of the unit's company in the unit's mode."""

    @abstractmethod
    async def get(self) -> DeliveryOptions:
        """The options as set; the defaults when never set."""

    @abstractmethod
    async def put(self, options: DeliveryOptions) -> None:
        """Set the options to ``options``, at the unit's time."""


class Deliveries(Protocol, metaclass=ABCMeta):
    """The unit's queue of the ``do_ecf.deliver`` job."""

    @abstractmethod
    def enqueue(self, document: UUID) -> None:
        """Deliver ``document`` once the unit commits: a fresh job, unless one is waiting or running."""
