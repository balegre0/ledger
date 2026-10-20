"""The ports the kit's users agree on (spec §8.4 ``ports``): the signer, the company directory, the archive.

``SignerPort``: the signer module's gRPC client implements it in every app that is not the signer (spec §3.7);
a channel reads it from its ports (``ports.get(SIGNER_PORT)``) and never holds a key. A request names the
tenant (its organization, for row-level security; its company and mode, which choose the certificate), the
signature profile and, for a document, the moment it states it was signed (``signed_at``): the signer refuses
unless the certificate covers that moment and its own clock agrees with it. An answer names the certificate
that signed, for the channel's archive.

``CompanyDirectory``: ``fiscal`` implements it over identity (a channel cannot read ``IdentityApi``): which
company a tax id names on a channel's environment (the receiver's question), a company's legal name and
primary tax id, and, as a ``PermissionSource``, what a principal may do in a company (the channel's routes).

``ArchivePort``: the app binds it over ``platform.blobs`` (spec §6.8): write-once objects kept until their
retention date, read back only against the SHA-256 recorded for them. PostgreSQL keeps keys and digests, the
archive keeps the bytes; ``archive_key`` names an object by whose it is, what it is and its digest.

Requests and answers are built by our own code from values it holds, so a value that breaks a rule here is a
defect of that code (``InvalidValueError``, internal), never a client's input. A document or a seed is never
shown (``repr``) nor quoted in an error; a reference is what an audit row names (an e-NCF, a receipt id) and
must never be personal data.
"""

import hashlib
import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, Protocol, TypeIs, final, runtime_checkable
lazy from datetime import date, datetime
lazy from uuid import UUID

from ledger_kernel.errors import (
    ConflictError,
    InvalidInputError,
    InvalidValueError,
    LedgerError,
    UnavailableError,
)
from ledger_kernel.tenancy import Tenant
from ledger_platform.auth.access import PermissionSource
from ledger_platform.config.manifest import CHANNEL_KEY
from ledger_platform.runtime.keys import Key
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tax_ids import TaxId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import SystemCall

MAX_SEED_BYTES: Final = 16 * 1024
"""The largest seed signed: DGII's ``SemillaModel`` is a few hundred bytes."""
_REFERENCE: Final = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_AUDIENCE: Final = re.compile(r"[A-Za-z0-9.:\[\]-]{1,253}")
_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CHANNEL: Final = re.compile(CHANNEL_KEY)
_KIND: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")
_EXTENSION: Final = re.compile(r"[a-z0-9]{1,8}")
SHA256_BYTES: Final = 32
"""The size of a SHA-256 digest: what an archived object's ``sha256`` and ``ArchivePort.get``'s take."""


class SignatureProfile(StrEnum):
    """How a document is signed: a profile of the kit's ``xml.dsig``, never a country's name."""

    XMLDSIG_ENVELOPED_RSA_SHA256 = "xmldsig-enveloped-rsa-sha256"
    """Enveloped XMLDSig, C14N 1.0, SHA-256, RSA-SHA256: DGII's (``xml.dsig.PROFILES``)."""


def is_audience(value: str) -> bool:
    """Whether ``value`` may be a seed's audience: a host name or address, with its port if any.

    For a caller that derives audiences from its configuration, to refuse one when it is configured rather
    than at its first seed (``SeedToSign`` applies the same rule).
    """
    return _AUDIENCE.fullmatch(value) is not None


def _require_moded(owner: str, tenant: Tenant) -> None:
    if tenant.mode is None:
        raise InvalidValueError(field=f"{owner}.tenant", requirement="a tenant in a mode")


def _require_aware(name: str, moment: datetime) -> None:
    if moment.utcoffset() is None:
        raise InvalidValueError(field=name, requirement="an aware datetime")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class XmlToSign:
    """A document to sign for ``tenant``, in ``profile``, at the moment it states."""

    tenant: Tenant
    """Its organization, company and mode: a mode is required (it chooses the certificate)."""
    profile: SignatureProfile
    document: bytes = field(repr=False)
    signed_at: datetime
    """Aware: the moment the document states it was signed (DGII's ``FechaHoraFirma``)."""
    reference: str
    """What the audit names (an e-NCF): 1 to 64 characters of ``A-Z a-z 0-9 . _ : -``, never personal data."""

    def __post_init__(self) -> None:
        _require_moded("xml_to_sign", self.tenant)
        _require_aware("xml_to_sign.signed_at", self.signed_at)
        if _REFERENCE.fullmatch(self.reference) is None:
            raise InvalidValueError(
                field="xml_to_sign.reference", requirement="1 to 64 characters of A-Z a-z 0-9 . _ : -"
            )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SeedToSign:
    """An authentication seed to sign for ``tenant``, in ``profile``, for ``audience``."""

    tenant: Tenant
    """Its organization, company and mode: a mode is required (it chooses the certificate)."""
    profile: SignatureProfile
    seed: bytes = field(repr=False)
    """At most ``MAX_SEED_BYTES``."""
    audience: str
    """The host the seed came from (the authority's, a buyer's receiver), audited: a host name or address,
    with its port if any."""

    def __post_init__(self) -> None:
        _require_moded("seed_to_sign", self.tenant)
        if len(self.seed) > MAX_SEED_BYTES:
            raise InvalidValueError(field="seed_to_sign.seed", requirement=f"at most {MAX_SEED_BYTES} bytes")
        if not is_audience(self.audience):
            raise InvalidValueError(field="seed_to_sign.audience", requirement="a host, with its port if any")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Signed:
    """A document or seed signed, and the certificate that signed it."""

    document: bytes = field(repr=False)
    certificate: UUID
    """Which certificate signed (the signer's internal id): the channel archives it with the document."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateValidity:
    """When the certificate that signs for a tenant may sign, both ends included: no key, no subject."""

    certificate: UUID
    not_before: datetime
    not_after: datetime

    def __post_init__(self) -> None:
        _require_aware("certificate_validity.not_before", self.not_before)
        _require_aware("certificate_validity.not_after", self.not_after)
        if self.not_after < self.not_before:
            raise InvalidValueError(
                field="certificate_validity.not_after", requirement="no earlier than not_before"
            )

    def covers(self, moment: datetime) -> bool:
        """Whether the certificate may sign at ``moment`` (aware), both ends included."""
        _require_aware("certificate_validity.covers", moment)
        return self.not_before <= moment <= self.not_after


@runtime_checkable
class SignerPort(Protocol, metaclass=ABCMeta):
    """Signatures for a tenant, by the certificate it has installed for the request's mode.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach. Each
    raises the errors below; an implementation sets its own deadline (at most 10 s).
    """

    @abstractmethod
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        """``request.document`` signed in ``request.profile``, the signature its root's last child.

        ``SigningCertificateMissingError``: no active certificate serves the tenant's company and mode;
        ``SigningCertificateNotValidError``: it does not cover ``signed_at`` and the signer's skew after it;
        ``SigningClockSkewError``: ``signed_at`` is too far from the signer's clock;
        ``SignatureRefusedError``: the document cannot be signed; ``SignerUnavailableError``: the signer could
        not be reached.
        """

    @abstractmethod
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        """``request.seed`` signed in ``request.profile``, as ``sign_xml`` signs, the audience audited."""

    @abstractmethod
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        """When the certificate serving ``tenant``'s company and mode may sign; ``None`` without one.

        ``tenant`` names a mode (it chooses the certificate): a tenant without one is refused
        (``InvalidValueError``, a defect of the caller), never answered with either mode's certificate.
        """


def is_signer_port(value: object) -> TypeIs[SignerPort]:
    """Whether ``value`` implements ``SignerPort``."""
    return isinstance(value, SignerPort)


SIGNER_PORT: Final = Key("SignerPort", is_signer_port)
"""``SignerPort`` under its name: how the signer module provides it and how a channel reads it."""


class SignerUnavailableError(UnavailableError):
    """The signer could not be reached, or is not configured: the same call may succeed later."""

    code: ClassVar[str] = "signer_unavailable"

    def __init__(self) -> None:
        super().__init__("the signer is unavailable")


class SigningCertificateMissingError(ConflictError):
    """No active certificate serves the company in the request's mode."""

    code: ClassVar[str] = "signing_certificate_missing"

    def __init__(self) -> None:
        super().__init__("no active certificate serves the company in this mode")


class SigningCertificateNotValidError(ConflictError):
    """The certificate does not cover the moment the document states and the skew allowed after it."""

    code: ClassVar[str] = "signing_certificate_not_valid"

    def __init__(self) -> None:
        super().__init__("the signing certificate is not valid at the moment the document states")


class SigningClockSkewError(ConflictError):
    """The moment the document states is too far from the signer's clock."""

    code: ClassVar[str] = "signing_clock_skew"

    def __init__(self) -> None:
        super().__init__("the moment the document states is too far from the signer's clock")


class SignatureRefusedError(InvalidInputError):
    """The document cannot be signed; ``reason`` is the signer's code for why (``xml_document_signed``...)."""

    code: ClassVar[str] = "signature_refused"

    def __init__(self, reason: str) -> None:
        if _CODE.fullmatch(reason) is None:
            raise InvalidValueError(field="signature_refused.reason", requirement="a snake_case code")
        self.reason: str = reason
        super().__init__(f"the signer refused to sign the document ({reason})")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceivingCompany:
    """A company as the directory knows it, in a mode: its legal name and primary tax id (neither shown)."""

    organization: OrganizationId
    company: CompanyId
    mode: Mode
    legal_name: str = field(repr=False)
    tax_id: TaxId = field(repr=False)

    @property
    def tenant(self) -> Tenant:
        """The company in its mode: the scope of what is done for it."""
        return Tenant(self.organization, self.company, self.mode)


@runtime_checkable
class CompanyDirectory(PermissionSource, Protocol, metaclass=ABCMeta):
    """Companies as channels need them (see the module), and what principals may do in them."""

    @abstractmethod
    async def receiving_company(
        self, call: SystemCall, *, tax_id: TaxId, channel: str, environment: str
    ) -> ReceivingCompany | None:
        """The company ``tax_id`` names, enrolled on ``channel`` in ``environment``; ``None`` if none.

        Its mode is the one the enrollment's environment implies; a disabled enrollment names no company.
        """

    @abstractmethod
    async def company(self, call: SystemCall, company: CompanyId) -> ReceivingCompany:
        """``company`` of the call's organization, in the call's mode.

        ``ledger_kernel.access.CompanyNotFoundError`` when the organization has no such company.
        """


def is_company_directory(value: object) -> TypeIs[CompanyDirectory]:
    """Whether ``value`` implements ``CompanyDirectory``."""
    return isinstance(value, CompanyDirectory)


COMPANY_DIRECTORY: Final = Key("CompanyDirectory", is_company_directory)
"""``CompanyDirectory`` under its name: how fiscal provides it and how a channel reads it."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ArchivedObject:
    """An object the archive holds: its key, the SHA-256 of its bytes and their size."""

    key: str
    sha256: bytes
    size: int

    def __post_init__(self) -> None:
        if len(self.sha256) != SHA256_BYTES:
            raise InvalidValueError(field="archived_object.sha256", requirement="a SHA-256 digest (32 bytes)")
        if self.size < 0:
            raise InvalidValueError(field="archived_object.size", requirement="0 or more")


@runtime_checkable
class ArchivePort(Protocol, metaclass=ABCMeta):
    """Write-once storage kept until a retention date (see the module).

    The contract every implementation keeps (the platform's stores prove it with their own tests):

    - an object is never overwritten nor deleted before its retention date;
    - ``put_once`` on a key that holds the same bytes answers the stored object and changes nothing: another
      ``retain_until`` neither extends nor shortens the retention it was stored with;
    - ``put_once`` on a key that holds other bytes raises ``ArchiveDigestMismatchError`` (a key names its
      bytes' digest, ``archive_key``: other bytes under it are a defect or a tampering);
    - ``get`` on a key that holds nothing raises ``ArchiveObjectMissingError``; a ``sha256`` that is not
      ``SHA256_BYTES`` long is refused (``InvalidValueError``) before the archive is asked;
    - ``ArchiveUnavailableError`` when the archive could not be reached: the same call may succeed later.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def put_once(
        self, key: str, content: bytes, *, retain_until: date, media_type: str
    ) -> ArchivedObject:
        """Store ``content`` under ``key`` until ``retain_until``, never to be changed or deleted before."""

    @abstractmethod
    async def get(self, key: str, *, sha256: bytes) -> bytes:
        """The bytes under ``key``, checked against ``sha256`` (``ArchiveDigestMismatchError`` otherwise)."""


def is_archive_port(value: object) -> TypeIs[ArchivePort]:
    """Whether ``value`` implements ``ArchivePort``."""
    return isinstance(value, ArchivePort)


ARCHIVE_PORT: Final = Key("ArchivePort", is_archive_port)
"""``ArchivePort`` under its name: how the app binds it and how a channel reads it."""


def archive_key(*, channel: str, tenant: Tenant, kind: str, content: bytes, extension: str) -> str:
    """``<channel>/<mode>/<organization>/<company>/<kind>/<sha256 hex>.<extension>``: where ``content`` goes.

    ``tenant`` is the company in its mode the object belongs to; ``kind`` is the channel's word for what the
    object is (DO's ``ArchiveKind``: ``ecf``, ``arecf-refused``...), a path segment and nothing more. The
    same bytes of one kind for one company in one mode always get one key.
    """
    mode = tenant.mode
    if mode is None:
        raise InvalidValueError(field="archive_key.tenant", requirement="a tenant in a mode")
    if _CHANNEL.fullmatch(channel) is None:
        raise InvalidValueError(field="archive_key.channel", requirement="a channel key")
    if _KIND.fullmatch(kind) is None:
        raise InvalidValueError(
            field="archive_key.kind",
            requirement="1 to 32 lower-case letters, digits or hyphens, led by a letter",
        )
    if _EXTENSION.fullmatch(extension) is None:
        raise InvalidValueError(
            field="archive_key.extension", requirement="1 to 8 lower-case letters or digits"
        )
    digest = hashlib.sha256(content).hexdigest()
    return f"{channel}/{mode.value}/{tenant.organization}/{tenant.company}/{kind}/{digest}.{extension}"


class ArchiveUnavailableError(UnavailableError):
    """The archive could not be reached: the same call may succeed later."""

    code: ClassVar[str] = "archive_unavailable"

    def __init__(self) -> None:
        super().__init__("the archive is unavailable")


class ArchiveDigestMismatchError(LedgerError):
    """An archived object's bytes are not those whose digest was recorded: a defect or a tampering."""

    code: ClassVar[str] = "archive_digest_mismatch"

    def __init__(self) -> None:
        super().__init__("an archived object's bytes do not match the digest recorded for them")


class ArchiveObjectMissingError(LedgerError):
    """An object the archive was asked for is not there: what PostgreSQL recorded was never stored."""

    code: ClassVar[str] = "archive_object_missing"

    def __init__(self) -> None:
        super().__init__("an archived object is missing")
