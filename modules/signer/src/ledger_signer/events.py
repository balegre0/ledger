"""What signer announces (§3.5): its certificates installed, retired and about to expire.

Each is about one certificate (its subject, and its ``certificate``), scoped to the certificate's company
without a mode: a certificate is shared by its company's modes (it says which it serves). Backends, modes and
reasons travel as their words: this module sits below the domain and knows none of its types. No event carries
anything secret or personal: no key, credential, subject name or fingerprint.
"""

from dataclasses import dataclass
from typing import ClassVar, final
lazy from datetime import datetime
lazy from uuid import UUID

from ledger_kernel.errors import InvalidValueError
from ledger_kernel.events import Event
from ledger_kernel.tenancy import Tenant
lazy from ledger_kernel.tenancy import Mode


@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateEvent(Event):
    """What every event of signer shares: the certificate it is about, its subject (see the module)."""

    ABSTRACT: ClassVar[bool] = True
    certificate: UUID

    def __post_init__(self) -> None:
        scope = self.scope
        if not isinstance(scope, Tenant) or scope.mode is not None:
            raise InvalidValueError(
                field="certificate_event.scope", requirement="a company's tenant without a mode"
            )
        if self.subject != self.certificate:
            raise InvalidValueError(field="certificate_event.subject", requirement="the certificate")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateInstalled(CertificateEvent):
    """A certificate was installed for ``modes``; it took them from the certificates it ``replaces``."""

    TYPE: ClassVar[str] = "signer.certificate.installed"
    VERSION: ClassVar[int] = 1
    modes: tuple[Mode, ...]
    """The modes it serves, one or both."""
    backend: str
    """``local`` or ``remote``."""
    not_after: datetime
    replaces: tuple[UUID, ...]
    """The active certificates that served one of its modes before it: each serves the rest, or is retired."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateRetired(CertificateEvent):
    """A certificate was retired: it never signs again."""

    TYPE: ClassVar[str] = "signer.certificate.retired"
    VERSION: ClassVar[int] = 1
    reason: str
    """``replaced``, ``retired`` or ``company_deactivated``."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateExpiring(CertificateEvent):
    """An active certificate expires in ``days_left`` days (public ``certificate.expiring``)."""

    TYPE: ClassVar[str] = "signer.certificate.expiring"
    VERSION: ClassVar[int] = 1
    not_after: datetime
    days_left: int
