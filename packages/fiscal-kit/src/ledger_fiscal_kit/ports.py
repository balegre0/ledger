"""The ports the kit's users agree on (spec §8.4 ``ports``): today the signer's, ``SignerPort``.

The signer module's gRPC client implements ``SignerPort`` in every app that is not the signer (spec §3.7); a
channel reads it from its ports (``ports.get(SIGNER_PORT)``) and never holds a key. A request names the
tenant (its organization, for row-level security; its company and mode, which choose the certificate), the
signature profile and, for a document, the moment it states it was signed (``signed_at``): the signer refuses
unless the certificate covers that moment and its own clock agrees with it. An answer names the certificate
that signed, for the channel's archive.

Requests and answers are built by our own code from values it holds, so a value that breaks a rule here is a
defect of that code (``InvalidValueError``, internal), never a client's input. A document or a seed is never
shown (``repr``) nor quoted in an error; a reference is what an audit row names (an e-NCF, a receipt id) and
must never be personal data.
"""

import re
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Final, Protocol, TypeIs, final, runtime_checkable
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import ConflictError, InvalidInputError, InvalidValueError, UnavailableError
from ledger_platform.runtime.keys import Key
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import SystemCall

MAX_SEED_BYTES: Final = 16 * 1024
"""The largest seed signed: DGII's ``SemillaModel`` is a few hundred bytes."""
_REFERENCE: Final = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_AUDIENCE: Final = re.compile(r"[A-Za-z0-9.:\[\]-]{1,253}")
_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


class SignatureProfile(StrEnum):
    """How a document is signed: a profile of the kit's ``xml.dsig``, never a country's name."""

    XMLDSIG_ENVELOPED_RSA_SHA256 = "xmldsig-enveloped-rsa-sha256"
    """Enveloped XMLDSig, C14N 1.0, SHA-256, RSA-SHA256: DGII's (``xml.dsig.PROFILES``)."""


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
        if _AUDIENCE.fullmatch(self.audience) is None:
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
