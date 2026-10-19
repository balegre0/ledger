"""The ports do-ecf-v1's use cases need; adapters implement them (spec §4.5).

``XmlSigner`` is the channel's narrow view of fiscal-kit's ``SignerPort`` (spec §8.4), bound to one issuing
company: ``adapters.signing.port.PortSigner`` implements it over the port (the signer module's gRPC client in
every app but the signer); in tests, the fake DGII and the spike CLI a key held in that process does. Whoever
holds the key decides whether its certificate may sign at the moment a document states, never the channel.
``Authority`` is DGII's web services for an emitter (spec §8.6.3), implemented by ``adapters.dgii.client``.
``EcfFormat`` is DGII's XML formats (written, checked against DGII's schemas, signatures verified),
implemented by ``adapters.xml.documents``.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, final
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.domain.errors import (
    EcfConflictError,
    EcfInvalidError,
    EcfUnavailableError,
)
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals


class XmlSigner(Protocol, metaclass=ABCMeta):
    """Signs DGII's documents with one issuing company's certificate, in DGII's profile.

    Every method raises, beside the errors it names: ``CertificateMissingError`` (the company has no
    certificate for the mode), ``CertificateNotValidError`` (it does not cover the moment of signing and the
    skew after it), ``SigningSkewError`` (the signer's clock disagrees with that moment) and
    ``SignerUnavailableError`` (the signer could not be reached). Each is a pack error (``EcfError``).
    """

    @abstractmethod
    async def sign_document(self, document: bytes, *, signed_at: datetime, reference: str) -> bytes:
        """``document`` with DGII's enveloped signature appended as its root's last child.

        ``signed_at``: the moment the document states (``FechaHoraFirma``); ``reference``: its e-NCF, which
        the signer's audit names. ``DocumentUnsignableError`` when the profile cannot sign it.
        """

    @abstractmethod
    async def sign_seed(self, seed: bytes, *, audience: str) -> bytes:
        """DGII's or a receiver's authentication seed (``SemillaModel``), signed the same way.

        ``audience``: the host the seed came from (a host name, with its port if any), which the signer's
        audit names. Anything but DGII's ``SemillaModel`` is refused before it reaches a key
        (``SeedMalformedError``), so a hostile receiver never gets another document signed as if it were a
        seed.
        """


class CertificateMissingError(EcfConflictError):
    """The issuing company has no certificate installed for the mode: install one, then sign again."""

    code: ClassVar[str] = "do_ecf_certificate_missing"

    def __init__(self) -> None:
        super().__init__("the issuing company has no signing certificate in this mode")


class CertificateNotValidError(EcfConflictError):
    """The signing certificate is expired or not yet valid at the moment of signing: renew or wait."""

    code: ClassVar[str] = "do_ecf_certificate_not_valid"

    def __init__(self) -> None:
        super().__init__("the signing certificate is not valid at the moment of signing")


class SignerUnavailableError(EcfUnavailableError):
    """The signer could not be reached, or is not configured: signing again later may succeed."""

    code: ClassVar[str] = "do_ecf_signer_unavailable"

    def __init__(self) -> None:
        super().__init__("the signer is unavailable")


class DocumentUnsignableError(EcfInvalidError):
    """The signer refused to sign the document; ``reason``: its code for why (``xml_document_signed``...)."""

    code: ClassVar[str] = "do_ecf_document_refused"

    def __init__(self, reason: str) -> None:
        self.reason: str = reason
        super().__init__(f"the signer refused to sign the document ({reason})")


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
    """DGII's verdict as it arrives: its code, its messages, ``secuenciaUtilizada``.

    ``rnc`` and ``encf``: the issuer and e-NCF DGII says the verdict is about; ``None`` where it names none.
    """

    code: int
    messages: tuple[DgiiMessage, ...]
    sequence_used: bool | None
    rnc: str | None = None
    encf: str | None = None


@final
@dataclass(frozen=True, slots=True)
class TrackId:
    """A track id DGII gave an e-NCF, and when DGII received the document."""

    track_id: str
    received_at: datetime


class Failure(StrEnum):
    """Why DGII could not be used (``AuthorityUnavailableError.failure``)."""

    UNREACHABLE = "unreachable"
    """No connection, or none in time."""
    BROKEN = "broken"
    """The connection broke, or an attempt's time ran out, after the request was sent."""
    SERVER_ERROR = "server_error"
    """A 5xx."""
    THROTTLED = "throttled"
    """A 429: DGII asks for fewer calls."""
    TIMED_OUT = "timed_out"
    """The call's overall deadline passed."""
    UNDOCUMENTED = "undocumented"
    """An answer DGII does not document for the service."""
    UNREADABLE = "unreadable"
    """An answer that does not read as the service's, in an encoding refused, or over its size limit."""
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
    """A document was sent and DGII may have taken it, its answer unknown: ask DGII before sending it again.

    ``reason``: the connection broke or the time ran out after sending (``broken``, ``timed_out``), DGII
    answered a 5xx (``server_error``), or a success that does not read as the service's (``unreadable``,
    ``undocumented``: no track id).
    """

    code: ClassVar[str] = "do_ecf_dgii_outcome_unknown"

    def __init__(self, service: str, reason: Failure) -> None:
        self.reason: Failure = reason
        super().__init__(
            f"DGII's {service}: the document may have reached DGII ({reason}); its outcome is unknown"
        )


class Authority(Protocol, metaclass=ABCMeta):
    """DGII's services for an emitter (spec §8.6.3)."""

    @abstractmethod
    def require_ready(self, environment: Environment) -> None:
        """Raise unless ``environment`` can be served now (hosts allowed, connections open); sends nothing."""

    @abstractmethod
    def attempt_limit(self) -> float:
        """Seconds one attempt at sending a document may take: after them, it has landed or never will."""

    @abstractmethod
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        """Send a signed e-CF to reception, once, never retried.

        An answer: DGII answered about it (``Received``, or ``Refused`` at the door). ``OutcomeUnknownError``:
        it may be at DGII. ``AuthorityUnavailableError`` (or any other error): DGII certainly did not take it.
        """

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
    ) -> tuple[TrackId, ...] | None:
        """Every track id DGII gave the e-NCF, oldest first; ``None`` where DGII has no such service."""


class EcfFormat(Protocol, metaclass=ABCMeta):
    """DGII's XML formats: documents written and checked against DGII's schemas.

    A schema check raises fiscal-kit's errors, not the pack's (``EcfError``), so ``Emitter.sign`` lets them
    pass as they are: ``SchemaViolationError`` (invalid: DGII's schema refuses the document),
    ``SchemaDriftError`` (internal: a vendored schema is not as pinned) and ``XmlDocumentError`` (invalid: the
    bytes are not a document read hardened). ``signed`` also raises the verifier's signature errors.
    """

    @abstractmethod
    def ecf(self, draft: EcfDraft, totals: Totals, *, signed_at: datetime) -> bytes:
        """``draft``'s unsigned e-CF, checked as it will be once signed."""

    @abstractmethod
    def rfce(self, draft: EcfDraft, totals: Totals, *, security_code: str) -> bytes:
        """``draft``'s unsigned RFCE, checked as it will be once signed."""

    @abstractmethod
    def signed(self, draft: EcfDraft, signed: bytes, *, rfce: bool) -> str:
        """``signed`` checked against its schema and its signature verified; its signature value."""
