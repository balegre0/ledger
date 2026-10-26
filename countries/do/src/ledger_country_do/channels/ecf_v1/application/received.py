"""What a company received (spec §3.8; Decision 16): the e-CF other emitters sent it, and where they are kept.

``ReceivedDocuments`` are the unit's company's, in the unit's mode: each e-NCF of an issuer once per
environment (``ReceivedExistsError`` for a second one, recorded meanwhile by a reception that raced).
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol, final
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.errors import EcfConflictError
lazy from ledger_country_do.channels.ecf_v1.application.ports import Archived
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import InboundEcf


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
    async def add(self, document: ReceivedDocument) -> None:
        """Keep a new received document (``ReceivedExistsError`` if its e-NCF was received meanwhile)."""
