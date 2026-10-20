"""The shapes of signer's public API: what clients send (unknown fields refused) and what they receive.

A certificate is named by its TypeID (``crt_…``, §9.4), formatted and read only here; times are ISO 8601 with
their offset, enumerations their lowercase words. A list answers a ``Page`` of certificates (§9.7). Nothing
here holds a key: a certificate's answer is its public part, and the credential a remote import sends is a
``SecretStr`` (never in a ``repr``, a log or the document's examples).
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from ledger_kernel.ids import InvalidPublicIdError, format_id, parse_id
from ledger_kernel.tenancy import Mode
from ledger_signer.api import CertificateId, CertificateNotFoundError
lazy from ledger_signer.api import CertificateView

CERTIFICATE_PREFIX: Final = "crt"
"""The public id prefix of a certificate (the manifest's ``[[ids]]``)."""
_SUFFIX: Final = "[0-7][0-9a-hjkmnp-tv-z]{25}"
"""A TypeID's 26 base32 characters (Crockford's alphabet, lower case): 128 bits, so the first is at most 7."""
CERTIFICATE_REF_PATTERN: Final = rf"^{CERTIFICATE_PREFIX}_{_SUFFIX}$"
"""What a certificate's public id looks like, as a schema states it."""
MAX_CREDENTIAL: Final = 2048
"""The longest credential a remote import takes."""
_MODES: Final = tuple(Mode)
"""The modes in one order, for every list of them."""

type CertificateRef = Annotated[str, StringConstraints(pattern=CERTIFICATE_REF_PATTERN)]
"""A certificate, by its public id (``crt_01h455vb4pex5vsknk084sn02q``)."""
type ModeWord = Literal["test", "live"]


def certificate_ref(certificate: CertificateId) -> str:
    """The public id of ``certificate``."""
    return format_id(CERTIFICATE_PREFIX, certificate)


def parse_certificate_ref(text: str) -> CertificateId:
    """The certificate ``text`` names; ``CertificateNotFoundError`` for anything but a public id of one."""
    try:
        return CertificateId(parse_id(CERTIFICATE_PREFIX, text))
    except InvalidPublicIdError:
        raise CertificateNotFoundError from None


class _Shape(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class RemoteImport(_Shape):
    """The certificate a certification entity signs with, installed for the request's company."""

    credential: SecretStr = Field(
        min_length=1,
        max_length=MAX_CREDENTIAL,
        description="The entity's credential for unattended signing; sealed at rest.",
    )
    modes: list[ModeWord] | None = Field(
        default=None,
        min_length=1,
        description="The modes the certificate serves: one or both. By default the request's mode.",
    )


class CertificateResource(_Shape):
    """A signing certificate of the company: its public part, never its key."""

    object: Literal["certificate"] = "certificate"
    id: CertificateRef
    livemode: bool = Field(description="Whether the request that answered it was in live mode.")
    modes: list[ModeWord] = Field(
        description="The modes it signs for while active; those it served last once retired (maybe none)."
    )
    backend: Literal["local", "remote"] = Field(
        description="local: its key is in our custody; remote: a certification entity signs with it."
    )
    status: Literal["active", "retired"]
    fingerprint_sha256: str = Field(description="SHA-256 of the certificate's DER encoding, in hexadecimal.")
    serial_number: str = Field(description="In hexadecimal.")
    subject: str = Field(description="Its holder (RFC 4514).")
    issuer: str = Field(description="Its certification authority (RFC 4514).")
    not_before: datetime
    not_after: datetime
    retired_at: datetime | None = Field(description="When it stopped signing; null while active.")
    retired_reason: Literal["replaced", "retired", "company_deactivated"] | None = Field(
        description=(
            "replaced: a newer certificate took its last mode; retired: the company retired it; "
            "company_deactivated: its company was deactivated. Null while active."
        )
    )
    version: int
    created_at: datetime = Field(description="When it was installed.")
    updated_at: datetime = Field(
        description=(
            "Approximate: when it was retired, else when it was installed. A mode a newer certificate took "
            "from it does not move it (no such moment is kept yet)."
        )
    )

    @classmethod
    def of(cls, view: CertificateView, *, mode: Mode) -> Self:
        """The answer of ``view`` to a request in ``mode``."""
        return cls.model_validate(
            {
                "id": certificate_ref(view.id),
                "livemode": mode is Mode.LIVE,
                "modes": [served.value for served in _MODES if served in view.modes],
                "backend": view.backend.value,
                "status": view.status.value,
                "fingerprint_sha256": view.fingerprint_sha256.hex(),
                "serial_number": view.serial_number,
                "subject": view.subject,
                "issuer": view.issuer,
                "not_before": view.not_before,
                "not_after": view.not_after,
                "retired_at": view.retired_at,
                "retired_reason": view.retired_reason,
                "version": view.version,
                "created_at": view.installed_at,
                "updated_at": view.retired_at or view.installed_at,
            }
        )


def modes_of(words: list[ModeWord] | None, *, default: Mode) -> frozenset[Mode]:
    """The modes ``words`` name; ``default`` alone when they name none."""
    return frozenset(Mode(word) for word in words) if words else frozenset({default})
