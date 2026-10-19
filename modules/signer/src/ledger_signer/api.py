"""signer's API for the other modules (§3.4): Protocols, frozen DTOs and the errors that cross it.

Callers receive a ``SignerApi`` from the composition root (``apis.get(SIGNER_API)``, once their manifest
declares the dependency): in every app but the signer it is the signer's gRPC client, in the signer's its
facade. Signatures are not here: a channel signs through fiscal-kit's ``SignerPort``, which signer provides.
Ids are internal UUIDs; the public ``crt_…`` form exists at the HTTP edge only.
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Final, Protocol, TypeIs, final, runtime_checkable
lazy from collections.abc import Sequence
lazy from datetime import datetime

from ledger_kernel.errors import InvalidInputError, NotFoundError
from ledger_platform.runtime.keys import Key
from ledger_signer.domain.certificates import BackendKind, CertificateId, CertificateStatus
lazy from ledger_kernel.ids import CompanyId, OrganizationId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import Call, SystemCall

__all__ = [
    "SIGNER_API",
    "BackendKind",
    "BackendNotOfferedError",
    "CertificateId",
    "CertificateNotFoundError",
    "CertificateStatus",
    "CertificateView",
    "SignerApi",
    "is_signer_api",
]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateView:
    """A certificate as the other modules and the HTTP edge see it: its public part, never its key."""

    id: CertificateId
    organization: OrganizationId
    company: CompanyId
    modes: frozenset[Mode]
    """The modes it serves: one or both while active; those it served last once retired (maybe none)."""
    backend: BackendKind
    status: CertificateStatus
    fingerprint_sha256: bytes
    """SHA-256 of its DER encoding."""
    serial_number: str
    """Hexadecimal."""
    subject: str = field(repr=False)
    """RFC 4514; it may name a person or hold a tax id, so it is never shown."""
    issuer: str
    """RFC 4514."""
    not_before: datetime
    not_after: datetime
    installed_at: datetime
    retired_at: datetime | None
    retired_reason: str | None
    """``replaced``, ``retired`` or ``company_deactivated``; ``None`` while active."""
    version: int


@runtime_checkable
class SignerApi(Protocol, metaclass=ABCMeta):
    """What the other modules may ask of signer.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def certificates(
        self, call: Call | SystemCall, company: CompanyId, mode: Mode
    ) -> Sequence[CertificateView]:
        """The company's certificates serving ``mode``, active first.

        fiscal-kit's ``SignerUnavailableError`` when the signer cannot be reached.
        """


def is_signer_api(value: object) -> TypeIs[SignerApi]:
    """Whether ``value`` implements ``SignerApi``."""
    return isinstance(value, SignerApi)


SIGNER_API: Final = Key("signer", is_signer_api)
"""signer's API under its name: how a module reads it and how signer binds it."""


class CertificateNotFoundError(NotFoundError):
    """The company has no certificate by that id (another company's is not found either)."""

    code: ClassVar[str] = "certificate_not_found"

    def __init__(self) -> None:
        super().__init__("certificate was not found")


class BackendNotOfferedError(InvalidInputError):
    """An import for a signing backend this deployment does not offer (``LEDGER_SIGNER_BACKENDS``)."""

    code: ClassVar[str] = "backend_not_offered"

    def __init__(self, backend: BackendKind) -> None:
        super().__init__(f"this deployment does not offer the {backend} backend")
