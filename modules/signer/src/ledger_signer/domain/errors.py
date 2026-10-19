"""What the domain of certificates refuses, each with its stable code (spec §9.6).

A certificate refused at its import is the client's input (``invalid``); a retired certificate, a document
whose moment the certificate does not cover and a moment too far from the signer's clock are the state of
things (``conflict``). The gRPC server answers each with its kind's status and its code; the client maps the
signing ones to fiscal-kit's port errors (one home per code). No message quotes a certificate's subject,
serial number or fingerprint.
"""

from typing import ClassVar

from ledger_kernel.errors import ConflictError, InvalidInputError


class CertificateKeyUnsupportedError(InvalidInputError):
    """A certificate whose key is not RSA, or is RSA of fewer bits than a signature needs."""

    code: ClassVar[str] = "certificate_key_unsupported"

    def __init__(self, min_bits: int) -> None:
        super().__init__(f"the certificate's key is not an RSA key of at least {min_bits} bits")


class CertificateNotForSigningError(InvalidInputError):
    """A certificate that may not sign documents: an authority's, or one whose key usage allows none."""

    code: ClassVar[str] = "certificate_not_for_signing"

    def __init__(self) -> None:
        super().__init__("the certificate may not sign documents")


class CertificateKeyMismatchError(InvalidInputError):
    """A certificate that is not the key's (local) or not the one the entity signs with (remote)."""

    code: ClassVar[str] = "certificate_key_mismatch"

    def __init__(self) -> None:
        super().__init__("the certificate does not belong to the key it came with")


class CertificateExpiredError(InvalidInputError):
    """A certificate whose validity has ended."""

    code: ClassVar[str] = "certificate_expired"

    def __init__(self) -> None:
        super().__init__("the certificate has expired")


class CertificateNotYetValidError(InvalidInputError):
    """A certificate whose validity has not begun."""

    code: ClassVar[str] = "certificate_not_yet_valid"

    def __init__(self) -> None:
        super().__init__("the certificate is not valid yet")


class CertificateRetiredError(ConflictError):
    """A retired certificate asked to sign: it never signs again."""

    code: ClassVar[str] = "certificate_retired"

    def __init__(self) -> None:
        super().__init__("the certificate is retired")


class NotCoveredError(ConflictError):
    """The certificate does not cover the moment a document states and the skew after it.

    fiscal-kit's ``SigningCertificateNotValidError`` at the port.
    """

    code: ClassVar[str] = "signer_window_not_covered"

    def __init__(self) -> None:
        super().__init__(
            "the certificate does not cover the moment the document states and the skew after it"
        )


class ClockSkewError(ConflictError):
    """The moment a document states is too far from the signer's clock.

    fiscal-kit's ``SigningClockSkewError`` at the port.
    """

    code: ClassVar[str] = "signer_clock_skew"

    def __init__(self) -> None:
        super().__init__("the moment the document states is too far from the signer's clock")
