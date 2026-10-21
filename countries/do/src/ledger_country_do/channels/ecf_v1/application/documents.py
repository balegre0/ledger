"""A company's e-CF documents as the use cases see them, and the ports that keep and move them (§8.6.4).

An ``EcfDocument`` is the e-CF of one invoice in one mode (Decision 35): its number, the range it came from
(and that range's last day, read with it), where it stands, the route DGII receives it by, and once signed,
its ``Signature``: when, its security code and QR, and where the archive keeps what was signed. It keeps the
``FiscalDraft`` it was numbered for, so every signature signs what the request checked.

``Documents`` are the unit's company's documents in the unit's mode; ``Advances`` the unit's queue of the
``do_ecf.advance`` job, which takes each document on from where it stands.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Protocol, final
lazy from collections.abc import Collection, Mapping
lazy from datetime import date, datetime
lazy from uuid import UUID

lazy from ledger_country_do.channels.ecf_v1.application.ports import Archived
lazy from ledger_country_do.channels.ecf_v1.domain.ecf import DeliveryState, EcfDocumentState, EmissionMode
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.references import AcceptedEcf
lazy from ledger_country_do.channels.ecf_v1.domain.routing import Route
lazy from ledger_fiscal_kit.drafts import DocumentRef, FiscalDraft


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
        """Keep a new document, numbered in this unit."""

    @abstractmethod
    async def changed(self, document: EcfDocument) -> None:
        """Keep ``document``'s new state, signature and version (its row locked in this unit)."""

    @abstractmethod
    async def accepted(self, invoices: Collection[DocumentRef]) -> Mapping[DocumentRef, AcceptedEcf]:
        """The e-CF DGII accepted (with or without observations) of those of ``invoices`` that have one."""


class Advances(Protocol, metaclass=ABCMeta):
    """The unit's queue of the ``do_ecf.advance`` job."""

    @abstractmethod
    def enqueue(self, document: UUID, *, at: datetime) -> None:
        """Take ``document`` on from ``at``: once the unit commits, and once per document."""
