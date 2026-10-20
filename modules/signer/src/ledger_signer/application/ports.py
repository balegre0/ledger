"""What the use cases need of the world: custody, the KMS, the certification entity, and signer's state.

**Custody** (``adapters.custody``) is the only code that touches a private key or a credential: it opens a
bundle (or asks the entity for its certificate), reads its facts and seals the secret under a fresh data key
the KMS wraps; to sign it unwraps that key, every time (no cache, plan Decision 18), and completes the
document fiscal-kit's ``dsig`` prepared. The ``KeyManagement`` and ``CertificationEntity`` contracts are those
any production adapter must honour (the plan's "Port contracts for the owner's choices").

**State** is read and changed in units (``SignerUnits``): one transaction scoped to a company, row-level
security limiting what its repositories see, every statement naming the company too. A unit records what the
transaction also decides: events (the outbox), audit rows, jobs and the import's idempotency key; it commits
them all or none. A unit never spans a call to the KMS or the entity (spec §3.6).

The methods are abstract so that they need no body: a ``...`` is a statement no test could reach. Secrets are
``Secret`` values here (the use cases import no pydantic): never in a ``repr``, a message or a record.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Final, Literal, Protocol, final, override
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence
lazy from contextlib import AbstractAsyncContextManager
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import (
    ConflictError,
    InvalidInputError,
    InvalidValueError,
    LedgerError,
    UnavailableError,
)
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.domain.certificates import BackendKind, Certificate, CertificateFacts, CertificateId
lazy from ledger_signer.events import CertificateEvent

CUSTODY_PURPOSE: Final = "signer.custody"
"""What a data key is for: the first entry of every encryption context the KMS binds."""
_HIDDEN: Final = "Secret('**********')"


@final
class Secret:
    """A password or a credential on its way to custody: shown as stars, revealed only by asking."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """The secret itself: for custody, which opens or seals with it, and nothing else."""
        return self._value

    @override
    def __repr__(self) -> str:
        return _HIDDEN

    @override
    def __str__(self) -> str:
        return _HIDDEN


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SealContext:
    """Whose secret is sealed: the certificate and its owners, bound to its data key by the KMS."""

    organization: OrganizationId
    company: CompanyId
    certificate: CertificateId

    def context(self) -> Mapping[str, str]:
        """The encryption context: the purpose, the organization, the company and the certificate."""
        return {
            "purpose": CUSTODY_PURPOSE,
            "organization": str(self.organization),
            "company": str(self.company),
            "certificate": str(self.certificate),
        }


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SealedMaterial:
    """A key (PKCS#8) or a credential sealed under a data key, and that data key as the KMS wrapped it."""

    sealed: bytes = field(repr=False)
    """AES-256-GCM: nonce, ciphertext and tag."""
    wrapped_data_key: bytes = field(repr=False)
    kms_key_id: str
    """The master key (and version) that wrapped the data key."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Admitted:
    """What custody read of a certificate it was handed, and the secret it sealed for it."""

    facts: CertificateFacts
    material: SealedMaterial


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DataKey:
    """A data key: its plaintext (AES-256) to seal with, and its wrapped form to keep."""

    plaintext: bytes = field(repr=False)
    """32 bytes; dropped by the caller as soon as it has sealed."""
    wrapped: bytes
    """Opaque; stored beside the sealed secret."""
    key_id: str
    """The master key (and version) that wrapped it, stored per certificate."""


class KeyManagement(Protocol, metaclass=ABCMeta):
    """The KMS that wraps data keys (the plan's port contract).

    It binds ``context`` cryptographically and refuses an unwrap under another; old master-key versions stay
    able to unwrap. ``KmsUnavailableError`` when it cannot be reached (retried by the caller),
    ``KmsRefusedError`` when it refuses (a deployment defect).
    """

    @abstractmethod
    async def new_data_key(self, context: Mapping[str, str]) -> DataKey:
        """A fresh data key, wrapped under ``context``."""

    @abstractmethod
    async def unwrap(self, wrapped: bytes, *, key_id: str, context: Mapping[str, str]) -> bytes:
        """The plaintext of a data key ``key_id`` wrapped under ``context``."""

    @abstractmethod
    async def ready(self) -> None:
        """Answer when the KMS can be used, without a key operation (readiness)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EntityCertificate:
    """The certificate chain a certification entity signs with for a credential."""

    chain: tuple[bytes, ...]
    """DER, the signing certificate first."""


class CertificationEntity(Protocol, metaclass=ABCMeta):
    """An INDOTEL-authorized certification entity holding a company's key (the remote backend's port).

    ``credential`` is opaque (its credential id and whatever authorizes unattended signing). The entity sees
    digests only, never a document. ``EntityUnavailableError`` when it cannot be reached;
    ``EntityRefusedError`` when it refuses the credential (revoked or expired).
    """

    @abstractmethod
    async def certificate(self, credential: bytes) -> EntityCertificate:
        """The chain the entity signs with for ``credential``."""

    @abstractmethod
    async def sign_digest(self, credential: bytes, digest: bytes) -> bytes:
        """RSASSA-PKCS1-v1_5 over ``digest`` (SHA-256) with the key ``credential`` names."""


class Custody(Protocol, metaclass=ABCMeta):
    """The only holder of keys and credentials (see the module). Each call may reach the KMS or the entity."""

    @abstractmethod
    async def admit_local(self, bundle: bytes, password: Secret, *, seal_for: SealContext) -> Admitted:
        """Open a PKCS#12 bundle, read its certificate's facts and seal its key.

        ``BundleUnreadableError`` for a wrong password or bytes that are no bundle (one code, so a password is
        never guessed by the error); ``BundleIncompleteError`` for a bundle without an RSA key and its
        certificate.
        """

    @abstractmethod
    async def admit_remote(self, credential: Secret, *, seal_for: SealContext) -> Admitted:
        """Ask the entity for the certificate ``credential`` signs with, read it and seal the credential."""

    @abstractmethod
    async def sign(
        self,
        material: SealedMaterial,
        *,
        backend: BackendKind,
        prepared: Prepared,
        certificate_der: bytes,
        seal_for: SealContext,
    ) -> bytes:
        """``prepared``'s document signed with the sealed key (local) or by the entity (remote).

        Unwraps the data key for this signature alone; makes RSASSA-PKCS1-v1_5 with SHA-256 over
        ``prepared.to_sign`` (the entity gets its SHA-256 digest); completes the document with the
        certificate, which ``dsig`` checks the value against (``SigningPairError`` when it is not its key's).
        """


class Certificates(Protocol, metaclass=ABCMeta):
    """The certificates of the unit's organization, each statement naming the company too."""

    @abstractmethod
    async def active(self, company: CompanyId, mode: Mode) -> Certificate | None:
        """The active certificate serving ``company`` in ``mode``, if any."""

    @abstractmethod
    async def active_of_company(self, company: CompanyId, *, for_update: bool) -> Sequence[Certificate]:
        """``company``'s active certificates; locked until the unit ends when ``for_update``."""

    @abstractmethod
    async def get(
        self, company: CompanyId, certificate: CertificateId, *, for_update: bool = False
    ) -> Certificate | None:
        """``company``'s certificate ``certificate``, if it has one; locked when ``for_update``."""

    @abstractmethod
    async def listed(self, company: CompanyId, mode: Mode, *, include_retired: bool) -> Sequence[Certificate]:
        """``company``'s certificates serving ``mode`` (or having served it last, once retired)."""

    @abstractmethod
    async def add(self, certificate: Certificate, material: SealedMaterial) -> None:
        """Keep a new certificate and its sealed secret.

        ``ActiveCertificateTakenError`` when another active certificate serves one of its modes (the partial
        unique index: another unit installed one meanwhile): the unit is then rolled back by its caller. The
        index may refuse at the statement or only at the commit, when the unit's block ends (a deferred
        write): the adapter raises ``ActiveCertificateTakenError`` for both; the use case retries the unit.
        """

    @abstractmethod
    async def save(self, certificate: Certificate) -> None:
        """Keep ``certificate`` at its new version; a retired one's sealed secret is deleted with it."""

    @abstractmethod
    async def material(self, certificate: Certificate) -> SealedMaterial:
        """The sealed secret of an active certificate (a retired one keeps none)."""


class Events(Protocol, metaclass=ABCMeta):
    """What the unit announces once it commits."""

    @abstractmethod
    def publish(self, event: CertificateEvent) -> None:
        """Announce ``event`` when, and only if, the unit commits (the outbox)."""


type AuditAction = Literal[
    "certificate.install", "certificate.retire", "certificate.sign_xml", "certificate.sign_seed"
]
"""What a use case records it did (spec §3.7: every signature is audited); the part before the dot is the
kind of subject."""


class Audit(Protocol, metaclass=ABCMeta):
    """Who did what, recorded with it (``audit_events``): it commits with the unit, or not at all.

    The actor is the acting call's, bound by whoever called the use case.
    """

    @abstractmethod
    def record(
        self,
        action: AuditAction,
        subject: UUID,
        *,
        detail: Mapping[str, str | int],
        before: object = None,
        after: object = None,
    ) -> None:
        """Record ``action`` on ``subject``; only hashes of ``before`` and ``after`` are kept.

        ``detail`` holds ids, codes and digests: never a document, a secret or a subject's name.
        """


type JobKind = Literal["signer.expiry_notice"]
"""The jobs the use cases enqueue."""


class Jobs(Protocol, metaclass=ABCMeta):
    """The jobs the unit enqueues, in its scope, when it commits."""

    @abstractmethod
    def enqueue(self, kind: JobKind, key: str, *, run_at: datetime, payload: Mapping[str, str | int]) -> None:
        """A job of ``kind`` keyed ``key`` (unique per kind: it embeds a certificate id) due at ``run_at``."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ImportClaim:
    """One use of an import's idempotency key: by whom, which key, and the hash of its request."""

    principal: str
    """The acting call's actor (``human:<subject>``...): the forwarded one, at the gRPC edge."""
    key: str
    request_hash: bytes
    """Plan Decision 9's fingerprint (``importing.fingerprint_of``)."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ImportOutcome:
    """The certificate an import installed, and whether it is the answer of an earlier one under its key."""

    view: CertificateView
    replayed: bool


class ImportKeys(Protocol, metaclass=ABCMeta):
    """An import's idempotency key (Decision 9): looked up first, then claimed in the unit that installs.

    The adapter wraps the platform's ``Idempotency``: ``answered`` is its ``stored``, ``once`` its ``once``,
    both with the route ``grpc ImportCertificate`` and the claim's principal, key and request hash.
    """

    @abstractmethod
    async def answered(self, claim: ImportClaim) -> ImportOutcome | None:
        """The answer stored under ``claim``'s key (``replayed``), or ``None``: nothing claimed or waited for.

        ``None`` before a first use, after the key expired and while a first use is in flight (``once`` then
        waits for it); ``IdempotencyKeyReusedError`` when the key was used for another request.
        """

    @abstractmethod
    async def once(self, claim: ImportClaim, work: Callable[[], Awaitable[CertificateView]]) -> ImportOutcome:
        """Claim ``claim``'s key in the unit and run ``work``, or replay the answer stored under it.

        A twin waits on the claim; ``IdempotencyKeyReusedError`` when the key was used for another request.
        Called before the work's own statements.
        """


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignerUnit:
    """One unit of work: signer's state as one transaction sees it, and what it records."""

    certificates: Certificates
    events: Events
    audit: Audit
    jobs: Jobs
    import_keys: ImportKeys
    now: datetime
    """The unit's time (the database's): everything it records happened then."""


class SignerUnits(Protocol, metaclass=ABCMeta):
    """Where the use cases get the units they work in.

    A unit the platform opened itself (a consumer's, around its inbox row) is not one of these: the adapter
    gives its signer view (``bind(uow) -> SignerUnit``, as identity's ``adapters.postgres.unit`` does), so
    that ``retire_company`` commits with the inbox row's deletion.
    """

    @abstractmethod
    def unit(self, tenant: Tenant) -> AbstractAsyncContextManager[SignerUnit]:
        """A unit scoped to ``tenant`` (its mode scopes keys and audit rows); it commits as it ends."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SignerRules:
    """The deployment's rules the use cases follow (from ``LEDGER_SIGNER_*``; bounds checked there too)."""

    backends: frozenset[BackendKind]
    """The backends it offers: an import for another is refused."""
    skew: timedelta
    """How far a document's moment and the signer's clock may differ (plan Decision 5)."""
    concurrency: int
    """Signatures computed at once."""
    notice_days: tuple[int, ...]
    """How many days before expiry a company is told."""

    def __post_init__(self) -> None:
        if not self.backends:
            raise InvalidValueError(field="signer_rules.backends", requirement="at least one backend")
        if self.skew <= timedelta(0):
            raise InvalidValueError(field="signer_rules.skew", requirement="longer than nothing")
        if self.concurrency < 1:
            raise InvalidValueError(field="signer_rules.concurrency", requirement="at least 1")
        if len(frozenset(self.notice_days)) != len(self.notice_days) or any(
            day < 0 for day in self.notice_days
        ):
            raise InvalidValueError(
                field="signer_rules.notice_days", requirement="distinct days, none negative"
            )


class BundleUnreadableError(InvalidInputError):
    """Bytes that are no PKCS#12 bundle, or a password that does not open it: one answer for both."""

    code: ClassVar[str] = "certificate_bundle_unreadable"

    def __init__(self) -> None:
        super().__init__("the certificate bundle could not be opened with the password given")


class BundleIncompleteError(InvalidInputError):
    """A bundle without an RSA private key and the certificate it signs with."""

    code: ClassVar[str] = "certificate_bundle_incomplete"

    def __init__(self) -> None:
        super().__init__("the certificate bundle does not hold an RSA key and its certificate")


class KmsUnavailableError(UnavailableError):
    """The KMS could not be reached: the same call may succeed later."""

    code: ClassVar[str] = "kms_unavailable"

    def __init__(self) -> None:
        super().__init__("the key management service is unavailable")


class KmsRefusedError(LedgerError):
    """The KMS refused (permission, another context, a disabled key): a defect of the deployment."""

    code: ClassVar[str] = "kms_refused"

    def __init__(self) -> None:
        super().__init__("the key management service refused the operation")


class EntityUnavailableError(UnavailableError):
    """The certification entity could not be reached: the same call may succeed later."""

    code: ClassVar[str] = "entity_unavailable"

    def __init__(self) -> None:
        super().__init__("the certification entity is unavailable")


class EntityRefusedError(ConflictError):
    """The certification entity refused the credential (revoked or expired): replace the certificate."""

    code: ClassVar[str] = "entity_refused"

    def __init__(self) -> None:
        super().__init__("the certification entity refused the credential")


class ActiveCertificateTakenError(ConflictError):
    """Another certificate was installed for one of the modes at the same time; retrying replaces it."""

    code: ClassVar[str] = "certificate_install_raced"

    def __init__(self) -> None:
        super().__init__("another certificate was installed for this company at the same time; retry")
