"""What the fake DGII holds, what it trusts, and how a test scripts its answers."""

import secrets
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from enum import IntEnum
from types import MappingProxyType
from typing import Final, Protocol, final, override
lazy from collections.abc import Callable, Collection, Mapping
lazy from datetime import datetime

lazy from cryptography import x509

from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import (
    STRICT,
    SignatureInvalidError,
    verify_enveloped,
)
from ledger_country_do_testing.signatures import verify_with_signxml
from ledger_fiscal_kit.xml.hardened import XmlDocumentError
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import VerificationProfile
lazy from ledger_testing.pki import Issued

TOKEN_LIFETIME: Final = timedelta(hours=1)
"""DT-DGII: a token lasts one hour, "for now" (its words)."""
SEED_LIFETIME: Final = timedelta(minutes=5)
"""The fake's choice (DGII does not document one); the receiver of spec §3.8 uses the same."""


class Status(IntEnum):
    """DGII's result codes (DT-DGII, "Consulta de resultado e-CF")."""

    NOT_FOUND = 0
    ACCEPTED = 1
    REJECTED = 2
    IN_PROCESS = 3
    ACCEPTED_CONDITIONALLY = 4


STATUS_WORDS: Final[Mapping[Status, str]] = MappingProxyType(
    {
        Status.NOT_FOUND: "No encontrado",
        Status.ACCEPTED: "Aceptado",
        Status.REJECTED: "Rechazado",
        Status.IN_PROCESS: "En Proceso",
        Status.ACCEPTED_CONDITIONALLY: "Aceptado Condicional",
    }
)


class SignatureCheck(Protocol, metaclass=ABCMeta):
    """Whether a document's enveloped signature holds, by a signer the fake trusts."""

    @abstractmethod
    def verify(self, signed: bytes) -> bool:
        """Whether ``signed``'s signature holds."""


@final
class SignxmlCheck(SignatureCheck):
    """signxml's verdict (``signatures.verify_with_signxml``); ``trusted`` ``None`` trusts any signer."""

    __slots__ = ("_trusted",)

    def __init__(self, *, trusted: Collection[x509.Certificate] | None) -> None:
        self._trusted = trusted

    @override
    def verify(self, signed: bytes) -> bool:
        return verify_with_signxml(signed, trusted=self._trusted)


@final
class VerifierCheck(SignatureCheck):
    """The channel's own verifier's verdict (``verify_enveloped``) under ``profile``.

    ``STRICT`` by default: what the fake receives is what our emitter signs, which must be DGII's profile
    exactly (*Firmado de e-CF*), not merely what a tolerant reader would let through. ``trusted`` ``None``
    trusts any signer whose signature holds.
    """

    __slots__ = ("_profile", "_trusted")

    def __init__(
        self, *, trusted: Collection[x509.Certificate] | None, profile: VerificationProfile = STRICT
    ) -> None:
        self._trusted = trusted
        self._profile = profile

    @override
    def verify(self, signed: bytes) -> bool:
        try:
            verified = verify_enveloped(signed, self._profile)
        except SignatureInvalidError, XmlDocumentError:
            return False
        return self._trusted is None or verified.certificate in self._trusted


@final
class EveryCheck(SignatureCheck):
    """A signature holds when every one of ``checks`` says it does."""

    __slots__ = ("_checks",)

    def __init__(self, *checks: SignatureCheck) -> None:
        self._checks = checks

    @override
    def verify(self, signed: bytes) -> bool:
        return all(check.verify(signed) for check in self._checks)


def dgii_check(*, trusted: Collection[x509.Certificate] | None) -> SignatureCheck:
    """What the fake checks by default: our verifier, strict, and signxml, which shares no code with it."""
    return EveryCheck(VerifierCheck(trusted=trusted), SignxmlCheck(trusted=trusted))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Scripted:
    """What the fake answers for one e-NCF."""

    status: Status = Status.ACCEPTED
    messages: tuple[tuple[str, str], ...] = ()
    """``(code, text)`` pairs, as DGII words its messages."""
    processing: int = 0
    """How many result queries answer "En Proceso" first (an RFCE is decided at once: ignored there)."""
    sequence_used: bool = True
    """DGII's ``secuenciaUtilizada``: ``False`` releases the number of a rejected document."""


@final
@dataclass(slots=True)
class Script:
    """What the fake answers, as a test sets it."""

    by_encf: dict[str, Scripted] = field(default_factory=dict[str, Scripted])
    default: Scripted = field(default_factory=Scripted)
    outage: int = 0
    """How many of the next requests answer 503; negative: every one, until set back to 0."""


@final
@dataclass(slots=True, kw_only=True)
class Received:
    """A document the fake received, and how it answers about it."""

    track_id: str | None
    environment: str
    host: str
    kind: str
    """``ecf`` (reception), ``rfce``, or ``buyer`` (the emisor-receptor simulator)."""
    file_name: str
    rnc: str
    encf: str
    security_code: str
    document: bytes = field(repr=False)
    received_at: datetime
    status: Status
    messages: tuple[tuple[str, str], ...]
    polls_left: int
    sequence_used: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryEntry:
    """A taxpayer of DGII's directory of electronic receivers (DT-DGII, "Consulta directorio")."""

    name: str
    rnc: str
    reception: str
    approval: str
    authentication: str | None


@final
class Credentials:
    """The seeds one authority (DGII, or a receiver) hands out and the tokens it issues."""

    __slots__ = ("_clock", "_seeds", "_tokens")

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._seeds: dict[str, datetime] = {}
        self._tokens: dict[str, datetime] = {}

    def seed(self) -> tuple[str, datetime]:
        """A new seed's value and the moment it was made."""
        value, now = secrets.token_urlsafe(36), self._clock()
        self._seeds[value] = now + SEED_LIFETIME
        return value, now

    def take_seed(self, value: str) -> bool:
        """Whether ``value`` is a seed handed out and not yet expired; it serves once."""
        expires = self._seeds.pop(value, None)
        return expires is not None and expires >= self._clock()

    def token(self) -> tuple[str, datetime, datetime]:
        """A new token, when it was issued and when it expires."""
        value, now = secrets.token_urlsafe(32), self._clock()
        self._tokens[value] = now + TOKEN_LIFETIME
        return value, now, now + TOKEN_LIFETIME

    def valid(self, authorization: str) -> bool:
        """Whether an ``Authorization`` header carries a live bearer token."""
        scheme, _, value = authorization.partition(" ")
        expires = self._tokens.get(value) if scheme.lower() == "bearer" else None
        return expires is not None and expires > self._clock()

    def revoke(self, token: str | None = None) -> None:
        """Forget ``token``, or every token issued."""
        if token is None:
            self._tokens.clear()
        else:
            self._tokens.pop(token, None)

    def issued(self) -> tuple[str, ...]:
        """Every live token, oldest first."""
        return tuple(self._tokens)


@final
class FakeState:
    """Everything the fake holds; tests read and set it."""

    __slots__ = (
        "approvals",
        "check",
        "clock",
        "dgii",
        "dgii_approvals",
        "directory",
        "public_url",
        "received",
        "receiver",
        "receiver_credentials",
        "receiver_requires_auth",
        "receiver_rncs",
        "script",
        "tamper_arecf",
        "voids",
    )

    def __init__(
        self, *, check: SignatureCheck, receiver: Issued, clock: Callable[[], datetime], public_url: str
    ) -> None:
        self.check: SignatureCheck = check
        self.receiver: Issued = receiver
        """The certificate the emisor-receptor simulator signs its ARECFs with."""
        self.clock: Callable[[], datetime] = clock
        self.public_url: str = public_url
        """Where the fake is reached from outside: the directory's URLs start with it."""
        self.script: Script = Script()
        self.received: list[Received] = []
        self.directory: dict[str, DirectoryEntry] = {}
        self.approvals: list[bytes] = []
        """ACECFs the emisor-receptor simulator received."""
        self.dgii_approvals: list[bytes] = []
        """ACECFs sent to DGII's own ``aprobacioncomercial``."""
        self.voids: list[bytes] = []
        """ANECFs DGII took (``anularrango``)."""
        self.receiver_requires_auth: bool = True
        self.receiver_rncs: frozenset[str] | None = None
        """The buyers the simulator receives for; ``None``: any."""
        self.tamper_arecf: bool = False
        """Answer ARECFs whose signature does not hold (for the buyer client's tests)."""
        self.dgii: Credentials = Credentials(clock)
        self.receiver_credentials: Credentials = Credentials(clock)

    def token_for_test(self) -> str:
        """A live DGII token, as if a seed had been validated."""
        return self.dgii.token()[0]
