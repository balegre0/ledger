"""What a company received (spec §3.8; Decision 16): the e-CF other emitters sent it, and where they are kept.

``ReceivedDocuments`` are the unit's company's, in the unit's mode: each e-NCF of an issuer once per
environment (``ReceivedExistsError`` for a second one, recorded meanwhile by a reception that raced). Once
the company decides of one commercially (spec §8.6.5 step 6, Task 23), the document keeps its
``OutgoingApproval``: the decision, the ACECF signed for it, and where sending that to the emitter and to
DGII stands.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol, final
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archived
lazy from ledger_country_do.channels.ecf_v1.domain.approval import Decided, Decision, Sending
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundEcf
lazy from ledger_fiscal_kit.status import AuthorityMessage
lazy from ledger_kernel.pagination import Page, PageRequest


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PartySending:
    """Where sending the company's ACECF to one party (the emitter, or DGII) stands."""

    state: Sending
    reason: str | None = None
    """Why it is not sent, or not sent yet: a code, never a tax id or a document."""
    sent_at: datetime | None = None
    """When the party took it (``sent``)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignedApproval:
    """The ACECF signed for a decision: its moment (``FechaHoraAprobacionComercial``), where it is kept."""

    at: datetime
    acecf: Archived


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class OutgoingApproval:
    """The company's commercial approval or rejection of a document it received, and its way out.

    ``requested_at``: when it decided (the seven days of Decision 21 run from there); ``attempts``: the runs
    that left a party to try again; ``signed``: the ACECF, once signed (``None`` before); DGII's
    ``dgii_messages`` as it answered, and ``dgii_started_at`` the submission's mark, committed before the one
    call that sends it to DGII.
    """

    decision: Decision
    reason: str | None
    """Why it rejected it (never on an approval)."""
    requested_at: datetime
    emitter: PartySending
    dgii: PartySending
    attempts: int = 0
    signed: SignedApproval | None = None
    dgii_messages: tuple[AuthorityMessage, ...] = ()
    dgii_started_at: datetime | None = None

    @property
    def settled(self) -> bool:
        """Whether nothing is left to send to either party."""
        return self.emitter.state.settled and self.dgii.state.settled


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceivedDocument:
    """An e-CF another emitter sent the company, received: what was read of it, where it is kept."""

    id: UUID
    environment: Environment
    ecf: InboundEcf
    received_at: datetime
    document: Archived
    """The document as it arrived."""
    receipt: Archived
    """The signed receipt (ARECF) it was answered with."""
    version: int
    approval: OutgoingApproval | None = None
    """The company's commercial decision of it, once it decided."""


class ReceivedExistsError(EcfConflictError):
    """The issuer's e-NCF was recorded in the environment meanwhile (a reception that raced this one)."""

    code: ClassVar[str] = "do_ecf_received_exists"

    def __init__(self) -> None:
        super().__init__("the e-CF was received already")


class ReceivedDocuments(Protocol, metaclass=ABCMeta):
    """The e-CF the unit's company received, in the unit's mode."""

    @abstractmethod
    async def find(self, environment: Environment, issuer_rnc: str, encf: Encf) -> ReceivedDocument | None:
        """The document ``issuer_rnc`` sent as ``encf`` in ``environment``, if it was received."""

    @abstractmethod
    async def get(self, document: UUID, *, lock: bool) -> ReceivedDocument | None:
        """The document, or ``None``; with ``lock``, its row locked until the unit ends (``FOR UPDATE``)."""

    @abstractmethod
    async def page(
        self,
        request: PageRequest,
        *,
        environment: Environment | None,
        issuer: str | None,
        decided: Decided | None,
    ) -> Page[ReceivedDocument]:
        """The documents, in id order: of ``environment``, from ``issuer`` and so ``decided`` when given."""

    @abstractmethod
    async def add(self, document: ReceivedDocument) -> None:
        """Keep a new received document (``ReceivedExistsError`` if its e-NCF was received meanwhile)."""

    @abstractmethod
    async def changed(self, document: ReceivedDocument) -> None:
        """Keep ``document``'s approval and version; its row is locked in this unit."""


class ApprovalJobs(Protocol, metaclass=ABCMeta):
    """The unit's queue of the ``do_ecf.approve`` job."""

    @abstractmethod
    def enqueue(self, document: UUID) -> None:
        """Send the company's decision of ``document`` once the unit commits."""
