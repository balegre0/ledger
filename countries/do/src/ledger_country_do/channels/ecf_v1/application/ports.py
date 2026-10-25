"""The ports do-ecf-v1's use cases need; adapters implement them (spec §4.5).

``Signatures`` is the channel's narrow view of fiscal-kit's ``SignerPort`` (spec §8.4): DGII's documents and
seeds signed as an issuing company, named on every call (``SigningAs``).
``adapters.signing.port.DocumentSigner`` implements it over the port (the signer module's gRPC client in
every app but the signer; in tests, the fake DGII and the spike CLI a key held in that process). Whoever
holds the key decides whether its certificate may sign at the moment a document states, never the channel.
``Authority`` is DGII's web services for an emitter (spec §8.6.3), each call made for an issuing company (its
token is that company's), implemented by ``adapters.dgii.client``. ``EcfFormat`` is DGII's XML formats
(written, checked against DGII's schemas, signatures verified), implemented by ``adapters.xml.documents``.

``SigningAs`` holds no call of the platform's (the use cases import no platform): the adapter that signs
makes each signature a ``SystemCall`` of its own, ``reason`` naming it, the tenant its scope.
"""

import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, Protocol, final
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_country_do.channels.ecf_v1.domain.errors import (
    EcfConflictError,
    EcfInvalidError,
    EcfUnavailableError,
)
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.polling import TrackId
lazy from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
lazy from ledger_country_do.channels.ecf_v1.domain.totals import Totals
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tenancy import Mode

_REASON: Final = re.compile(r"[a-z][a-z0-9._:-]{0,63}", re.ASCII)
_REASON_RULE: Final = "1 to 64 characters of a-z 0-9 . _ : -, a letter first"


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SigningAs:
    """Whom a DGII or buyer call is made for, and who signs for it: an issuing company in a mode.

    ``reason`` names every signature made for it (the signer's audit actor is ``system:<reason>``), so it is
    code-shaped and never personal data. DGII's tokens are kept per company, mode and environment.
    """

    organization: OrganizationId
    company: CompanyId
    mode: Mode
    reason: str

    def __post_init__(self) -> None:
        if _REASON.fullmatch(self.reason) is None:
            raise InvalidValueError(field="signing_as.reason", requirement=_REASON_RULE)

    @property
    def tenant(self) -> Tenant:
        """The company in its mode: the scope of every signature made for it."""
        return Tenant(self.organization, self.company, self.mode)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignedDocument:
    """A document signed, and which certificate signed it (the signer's id): the archive records both."""

    document: bytes = field(repr=False)
    certificate: UUID


class Signatures(Protocol, metaclass=ABCMeta):
    """Signs DGII's documents and seeds as ``who``, in DGII's profile.

    Every method raises, beside the errors it names: ``CertificateMissingError`` (the company has no
    certificate for the mode), ``CertificateNotValidError`` (it does not cover the moment of signing and the
    skew after it), ``SignerClockSkewError`` (the signer's clock disagrees with that moment), each a conflict
    an operator resolves, and ``SignerUnavailableError`` (the signer could not be reached: nothing was
    signed, try again later). Each is a pack error (``EcfError``).
    """

    @abstractmethod
    async def document(
        self, who: SigningAs, document: bytes, *, signed_at: datetime, reference: str
    ) -> SignedDocument:
        """``document`` with DGII's enveloped signature appended as its root's last child.

        ``signed_at``: the moment the document states (``FechaHoraFirma``); ``reference``: its e-NCF, which
        the signer's audit names. ``DocumentUnsignableError`` when the profile cannot sign it.
        """

    @abstractmethod
    async def seed(self, who: SigningAs, seed: bytes, *, audience: str) -> bytes:
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


class SignerClockSkewError(EcfConflictError):
    """The signer's clock disagrees with the moment a document states, beyond the skew it allows.

    Signing again states a fresh moment from the same clock: until one of the two clocks is fixed, every
    signature is refused (an operator's matter, not a retry's).
    """

    code: ClassVar[str] = "do_ecf_signer_clock_skew"

    def __init__(self) -> None:
        super().__init__("the signer's clock disagrees with the moment of signing")


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
class Acknowledged:
    """DGII took a commercial approval: its ``estado`` and messages, as it words them."""

    state: str
    messages: tuple[DgiiMessage, ...]


@final
@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """A receiver's service bases, from DGII's directory; ``authentication`` ``None`` if it publishes none.

    ``rnc`` is the taxpayer the entry names, which may not be the one asked about (see
    ``Authority.directory``): whoever sends there compares it first.
    """

    name: str
    rnc: str
    reception: str
    approval: str
    authentication: str | None


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
    """DGII's services for an emitter (spec §8.6.3), each call made for ``who`` (its token is ``who``'s).

    A service DGII does not publish in an environment (``consultatrackids``, ``anularrango`` and
    ``consultadirectorio`` in CerteCF) raises ``ServiceNotInEnvironmentError`` before anything is sent.
    """

    @abstractmethod
    def require_ready(self, environment: Environment) -> None:
        """Raise unless ``environment`` can be served now (hosts allowed, connections open); sends nothing."""

    @abstractmethod
    def attempt_limit(self) -> float:
        """Seconds one attempt at sending a document may take: after them, it has landed or never will."""

    @abstractmethod
    async def submit_ecf(
        self, who: SigningAs, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        """Send a signed e-CF to reception, once, never retried.

        An answer: DGII answered about it (``Received``, or ``Refused`` at the door). ``OutcomeUnknownError``:
        it may be at DGII. ``AuthorityUnavailableError`` (or any other error): DGII certainly did not take it.
        """

    @abstractmethod
    async def submit_rfce(
        self, who: SigningAs, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        """Send a signed RFCE; DGII decides at once. Never retried, as ``submit_ecf``."""

    @abstractmethod
    async def result(self, who: SigningAs, environment: Environment, track_id: str) -> Answer:
        """DGII's verdict on a received document (``consultaresultado``)."""

    @abstractmethod
    async def track_ids(
        self, who: SigningAs, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[TrackId, ...]:
        """Every track id DGII gave the e-NCF, oldest first (``consultatrackids``; not in CerteCF)."""

    @abstractmethod
    async def rfce_status(
        self, who: SigningAs, environment: Environment, issuer_rnc: str, encf: Encf, security_code: str
    ) -> Answer:
        """DGII's verdict on an RFCE sent (``consultarfce``), in codes of its own.

        Its codes are ``RfceQueryCode``'s, not ``consultaresultado``'s
        (``status_map.decide(..., service=DgiiService.RFCE_QUERY)``).
        """

    @abstractmethod
    async def void_ranges(
        self, who: SigningAs, environment: Environment, signed_anecf: bytes
    ) -> Answer | Refused:
        """Send a signed ANECF (``anularrango``; not in CerteCF), once, never retried, as ``submit_ecf``."""

    @abstractmethod
    async def commercial_approval(
        self, who: SigningAs, environment: Environment, signed_acecf: bytes
    ) -> Acknowledged | Refused:
        """Send a signed ACECF to DGII (``aprobacioncomercial``), once, never retried, as ``submit_ecf``."""

    @abstractmethod
    async def directory(self, who: SigningAs, environment: Environment, rnc: str) -> DirectoryEntry | None:
        """The receiver ``rnc`` in DGII's directory; ``None`` if DGII lists no entry (no electronic receiver).

        The entry naming ``rnc`` when DGII lists one; else the first DGII lists, naming another taxpayer
        (whatever DGII's reason): the caller compares ``DirectoryEntry.rnc`` and never sends ``rnc``'s
        documents there (not in CerteCF).
        """


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Archived:
    """Where the archive keeps a document, and the SHA-256 it was kept with: PostgreSQL keeps both."""

    key: str
    sha256: bytes = field(repr=False)


class ArchiveUnavailableError(EcfUnavailableError):
    """The archive could not keep a document now: nothing is recorded as signed until it does."""

    code: ClassVar[str] = "do_ecf_archive_unavailable"

    def __init__(self) -> None:
        super().__init__("the archive is unavailable")


class Archive(Protocol, metaclass=ABCMeta):
    """The legal archive (§6.8, Decisions 11-13): a company's signed documents, kept once, for ten years.

    A document is kept under a key its bytes name, so keeping the same bytes again is the same object, and
    other bytes are another; nothing kept is ever changed or removed. Each method raises
    ``ArchiveUnavailableError`` when the archive cannot be reached (keeping it again later may succeed).
    """

    @abstractmethod
    async def ecf(self, tenant: Tenant, signed: bytes) -> Archived:
        """``signed``, an e-CF of ``tenant``'s, kept."""

    @abstractmethod
    async def rfce(self, tenant: Tenant, signed: bytes) -> Archived:
        """``signed``, a consumer-invoice summary (RFCE) of ``tenant``'s, kept."""

    @abstractmethod
    async def read(self, archived: Archived) -> bytes:
        """The bytes kept as ``archived``, checked against its SHA-256 before they are answered (Decision 12).

        Bytes that do not match, or a key the archive does not hold, are the archive's defects: never sent.
        """


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
