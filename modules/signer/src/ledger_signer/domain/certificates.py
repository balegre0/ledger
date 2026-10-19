"""Certificates in custody: what a certificate must be to be admitted, the modes it serves, when it may sign.

The adapters read certificates (no cryptography here): the domain gets their facts (``CertificateFacts``) and
decides. A certificate belongs to a company and is shared by its modes: an active one serves one or both, and
installing one takes its modes from the company's active certificates that served them, each retired once it
serves none (``install``), so one active certificate serves a company in each mode. A certificate signs a
document only while it covers the moment the document states and the skew after it, and while that moment is
within the skew of the signer's clock (``Certificate.require_signable``, plan Decision 5).

Values that break these rules when our own code builds them are defects (``InvalidValueError``, internal); a
certificate the client sends that breaks them is refused with the errors of ``domain.errors``.
"""

from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from typing import Final, NewType, final
from uuid import UUID
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Mode
from ledger_signer.domain.errors import (
    CertificateExpiredError,
    CertificateKeyMismatchError,
    CertificateKeyUnsupportedError,
    CertificateNotForSigningError,
    CertificateNotYetValidError,
    CertificateRetiredError,
    ClockSkewError,
    NotCoveredError,
)
lazy from ledger_kernel.tenancy import Tenant

CertificateId = NewType("CertificateId", UUID)
"""A certificate in custody: its internal id (UUIDv7); its public form is ``crt_…`` at the HTTP edge only."""
MIN_RSA_BITS: Final = 2048
"""The smallest RSA key a certificate may sign with (fiscal-kit's ``xml.dsig.MIN_KEY_BITS``, checked again
there at every signature)."""
FINGERPRINT_BYTES: Final = 32
"""A SHA-256 digest."""
_NO_TIME: Final = timedelta(0)


class BackendKind(StrEnum):
    """Where a certificate's key is, and so who signs with it (spec §3.7)."""

    LOCAL = "local"
    """In our custody, sealed under a data key the KMS wraps."""
    REMOTE = "remote"
    """At an INDOTEL-authorized certification entity; we hold a credential for it."""


class CertificateStatus(StrEnum):
    """Whether a certificate may still sign."""

    ACTIVE = "active"
    RETIRED = "retired"
    """Replaced, retired by the company, or its company deactivated: it never signs again."""


class RetiredReason(StrEnum):
    """Why a certificate was retired."""

    REPLACED = "replaced"
    """Certificates installed after it took every mode it served."""
    RETIRED = "retired"
    """Its company retired it."""
    COMPANY_DEACTIVATED = "company_deactivated"
    """Its company was deactivated."""


def _require_aware(name: str, moment: datetime) -> None:
    if moment.utcoffset() is None:
        raise InvalidValueError(field=name, requirement="an aware datetime")


def _require_window(owner: str, not_before: datetime, not_after: datetime) -> None:
    _require_aware(f"{owner}.not_before", not_before)
    _require_aware(f"{owner}.not_after", not_after)
    if not_after <= not_before:
        raise InvalidValueError(field=f"{owner}.not_after", requirement="later than not_before")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateFacts:
    """What custody read from a certificate and its key (or the entity's certificate): no key, no secret."""

    der: bytes = field(repr=False)
    """The certificate, DER encoded."""
    fingerprint_sha256: bytes
    serial_number: str
    """Hexadecimal."""
    subject: str = field(repr=False)
    """RFC 4514; it may name a person or hold a tax id, so it is never shown."""
    issuer: str
    """RFC 4514."""
    not_before: datetime
    not_after: datetime
    rsa_bits: int | None
    """The key's size, or ``None`` when it is not an RSA key."""
    may_sign: bool
    """Its key usage allows a signature (fiscal-kit's ``xml.dsig.signing_problem`` finds none)."""
    is_authority: bool
    """It is a certification authority's (basic constraints ``CA``)."""
    matches_key: bool
    """It is the key's (local), or the certificate the entity signs with (remote)."""

    def __post_init__(self) -> None:
        if len(self.fingerprint_sha256) != FINGERPRINT_BYTES:
            raise InvalidValueError(
                field="certificate_facts.fingerprint_sha256", requirement="a SHA-256 digest"
            )
        _require_window("certificate_facts", self.not_before, self.not_after)


def admit(facts: CertificateFacts, *, now: datetime) -> None:
    """Refuse a certificate that cannot sign for its company now; the first rule it breaks, in this order.

    ``CertificateKeyUnsupportedError`` (not RSA, or under ``MIN_RSA_BITS``), ``CertificateNotForSigningError``
    (an authority's, or a key usage without a signature), ``CertificateKeyMismatchError``,
    ``CertificateExpiredError`` (``not_after`` is past or now), ``CertificateNotYetValidError``.
    """
    _require_aware("now", now)
    if facts.rsa_bits is None or facts.rsa_bits < MIN_RSA_BITS:
        raise CertificateKeyUnsupportedError(MIN_RSA_BITS)
    if facts.is_authority or not facts.may_sign:
        raise CertificateNotForSigningError
    if not facts.matches_key:
        raise CertificateKeyMismatchError
    if facts.not_after <= now:
        raise CertificateExpiredError
    if facts.not_before > now:
        raise CertificateNotYetValidError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Certificate:
    """A company's certificate in custody, at its version: the aggregate (see the module).

    An active certificate serves one mode or both. A retired one keeps the modes it served last, for the
    record, and serves none; it has the moment and the reason it was retired, an active one neither.
    """

    id: CertificateId
    tenant: Tenant
    """Its organization and company; no mode (it serves ``modes``)."""
    modes: frozenset[Mode]
    backend: BackendKind
    status: CertificateStatus
    der: bytes = field(repr=False)
    fingerprint_sha256: bytes
    serial_number: str
    subject: str = field(repr=False)
    issuer: str
    not_before: datetime
    not_after: datetime
    installed_at: datetime
    retired_at: datetime | None
    retired_reason: RetiredReason | None
    version: int

    def __post_init__(self) -> None:
        if self.tenant.mode is not None:
            raise InvalidValueError(
                field="certificate.tenant", requirement="a company's tenant without a mode"
            )
        if len(self.fingerprint_sha256) != FINGERPRINT_BYTES:
            raise InvalidValueError(field="certificate.fingerprint_sha256", requirement="a SHA-256 digest")
        _require_window("certificate", self.not_before, self.not_after)
        _require_aware("certificate.installed_at", self.installed_at)
        if self.version < 1:
            raise InvalidValueError(field="certificate.version", requirement="at least 1")
        retired = (self.retired_at, self.retired_reason)
        if self.status is CertificateStatus.ACTIVE:
            if not self.modes:
                raise InvalidValueError(
                    field="certificate.modes", requirement="one mode or both while active"
                )
            if retired != (None, None):
                raise InvalidValueError(field="certificate.retired_at", requirement="unset while active")
        elif self.retired_at is None or self.retired_reason is None:
            raise InvalidValueError(field="certificate.retired_at", requirement="set once retired")
        else:
            _require_aware("certificate.retired_at", self.retired_at)

    @property
    def is_active(self) -> bool:
        """Whether it may still sign."""
        return self.status is CertificateStatus.ACTIVE

    def serves(self, mode: Mode) -> bool:
        """Whether it signs for its company in ``mode``: it is active and ``mode`` is one of its modes."""
        return self.is_active and mode in self.modes

    def without(self, modes: frozenset[Mode], *, at: datetime) -> Certificate:
        """It, no longer serving ``modes`` from ``at``: retired ``replaced`` when it is left serving none.

        Unchanged (the same version) when it serves none of ``modes`` or is retired already.
        """
        if not self.is_active or self.modes.isdisjoint(modes):
            return self
        left = self.modes - modes
        if not left:
            return self.retired(RetiredReason.REPLACED, at=at)
        return replace(self, modes=left, version=self.version + 1)

    def retired(self, reason: RetiredReason, *, at: datetime) -> Certificate:
        """It, retired ``at`` for ``reason``; a retired one unchanged (its first reason and moment kept)."""
        if not self.is_active:
            return self
        return replace(
            self,
            status=CertificateStatus.RETIRED,
            retired_at=at,
            retired_reason=reason,
            version=self.version + 1,
        )

    def require_signable(self, *, signed_at: datetime, now: datetime, skew: timedelta) -> None:
        """Refuse a signature of a document stating ``signed_at``, by the signer's clock ``now``.

        ``CertificateRetiredError`` once retired (whatever the moments); ``ClockSkewError`` when ``signed_at``
        is more than ``skew`` from ``now``, either way; ``NotCoveredError`` unless the certificate is valid
        from ``signed_at`` to ``signed_at + skew``, both ends included. A moment far from the clock says
        nothing of the window, so the skew is checked first.
        """
        _require_aware("signed_at", signed_at)
        _require_aware("now", now)
        if skew < _NO_TIME:
            raise InvalidValueError(field="skew", requirement="no shorter than nothing")
        if not self.is_active:
            raise CertificateRetiredError
        if abs(now - signed_at) > skew:
            raise ClockSkewError
        if not (self.not_before <= signed_at and signed_at + skew <= self.not_after):
            raise NotCoveredError


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Installation:
    """A certificate installed, and the active certificates of its company that lost modes to it."""

    installed: Certificate
    changed: tuple[Certificate, ...]
    """Each at its new version: serving the modes it kept, or retired ``replaced`` (it kept none)."""

    @property
    def replaced(self) -> tuple[CertificateId, ...]:
        """The certificates that served one of its modes before it."""
        return tuple(certificate.id for certificate in self.changed)

    @property
    def retired(self) -> tuple[Certificate, ...]:
        """Those of ``changed`` left serving no mode, now retired."""
        return tuple(certificate for certificate in self.changed if not certificate.is_active)


def install(new: Certificate, active: Sequence[Certificate], *, at: datetime) -> Installation:
    """Install ``new``: each of ``active`` (its company's active certificates) loses ``new``'s modes.

    Never two active certificates serving one mode: ``new`` must be active and ``active`` of its company and
    active; after it, each mode is served by one of them at most (both are defects of the caller otherwise,
    ``InvalidValueError``).
    """
    if not new.is_active:
        raise InvalidValueError(field="install.new", requirement="an active certificate")
    if any(not certificate.is_active or certificate.tenant != new.tenant for certificate in active):
        raise InvalidValueError(field="install.active", requirement="active certificates of the same company")
    changed = tuple(
        certificate.without(new.modes, at=at)
        for certificate in active
        if not certificate.modes.isdisjoint(new.modes)
    )
    kept = (
        new,
        *changed,
        *(certificate for certificate in active if certificate.modes.isdisjoint(new.modes)),
    )
    for mode in Mode:
        if sum(certificate.serves(mode) for certificate in kept) > 1:
            raise InvalidValueError(field="install", requirement="one active certificate per mode")
    return Installation(installed=new, changed=changed)
