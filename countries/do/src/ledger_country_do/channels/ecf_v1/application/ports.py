"""The ports do-ecf-v1's use cases need; adapters implement them (spec §4.5).

``XmlSigner`` becomes fiscal-kit's ``SignerPort`` (spec §8.4) at integration, implemented over the signer
module's gRPC client; in the spike a key held in the test or CLI process implements it. ``Authority`` is
DGII's web services for an emitter (spec §8.6.3), implemented by ``adapters.dgii.client``.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, final

from ledger_country_do.channels.ecf_v1.domain.errors import EcfUnavailableError
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage


class XmlSigner(Protocol, metaclass=ABCMeta):
    """Signs DGII's documents with the issuing company's certificate, in DGII's profile."""

    @abstractmethod
    async def sign_document(self, document: bytes) -> bytes:
        """``document`` with DGII's enveloped signature appended as its root's last child."""

    @abstractmethod
    async def sign_seed(self, seed: bytes) -> bytes:
        """DGII's or a receiver's authentication seed (``SemillaModel``), signed the same way."""


@final
@dataclass(frozen=True, slots=True)
class Received:
    """DGII took the document: its track id."""

    track_id: str


@final
@dataclass(frozen=True, slots=True)
class Refused:
    """DGII refused the document at the door (HTTP 400): it never reached validation."""

    messages: tuple[DgiiMessage, ...]


type Reception = Received | Refused


@final
@dataclass(frozen=True, slots=True)
class Answer:
    """DGII's verdict as it arrives: its code, its messages, ``secuenciaUtilizada``."""

    code: int
    messages: tuple[DgiiMessage, ...]
    sequence_used: bool | None


class Failure(StrEnum):
    """Why DGII could not be used (``AuthorityUnavailableError.failure``)."""

    UNREACHABLE = "unreachable"
    """No connection, or none in time."""
    BROKEN = "broken"
    """The connection broke, or the time ran out, after a query was sent."""
    SERVER_ERROR = "server_error"
    """A 5xx or a 429."""
    UNDOCUMENTED = "undocumented"
    """An answer DGII does not document for the service."""
    UNREADABLE = "unreadable"
    """An answer that does not read as the service's, or over its size limit."""
    REFUSED = "refused"
    """DGII refused the signed seed (its signature or certificate)."""
    NOT_OPEN = "not_open"
    """The client was used outside ``opened``."""


class AuthorityUnavailableError(EcfUnavailableError):
    """DGII could not be reached or answered something it does not document; ``failure`` says which."""

    code: ClassVar[str] = "do_ecf_dgii_unavailable"

    def __init__(self, failure: Failure, service: str) -> None:
        self.failure: Failure = failure
        super().__init__(f"DGII's {service}: {failure}")


class OutcomeUnknownError(EcfUnavailableError):
    """The connection broke after a document may have reached DGII: ask DGII before sending it again."""

    code: ClassVar[str] = "do_ecf_dgii_outcome_unknown"

    def __init__(self, service: str) -> None:
        super().__init__(f"DGII's {service}: the connection broke after sending; the outcome is unknown")


class Authority(Protocol, metaclass=ABCMeta):
    """DGII's services for an emitter (spec §8.6.3)."""

    @abstractmethod
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        """Send a signed e-CF to reception; never retried (``OutcomeUnknownError``: the connection broke)."""

    @abstractmethod
    async def submit_rfce(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        """Send a signed RFCE; DGII decides at once. Never retried, as ``submit_ecf``."""

    @abstractmethod
    async def result(self, environment: Environment, track_id: str) -> Answer:
        """DGII's verdict on a received document."""

    @abstractmethod
    async def track_ids(
        self, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[str, ...] | None:
        """Every track id DGII gave the e-NCF, oldest first; ``None`` where DGII has no such service."""
