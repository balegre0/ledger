"""What custody reads of a certificate for the domain to decide on (``CertificateFacts``): no secret in it.

``may_sign`` is fiscal-kit's rule of what may sign (``dsig.signing_problem``: no authority, a key usage that
allows a signature), and one more: the certificate's issuer did not sign it with SHA-1 or MD5, whose
collisions let whoever forges one make a certificate that names anyone. Such a certificate is refused at its
import as one that may not sign (``certificate_not_for_signing``).
"""

from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import SignatureAlgorithmOID

from ledger_fiscal_kit.xml import dsig
from ledger_signer.domain.certificates import CertificateFacts

BROKEN_SIGNATURES: Final = frozenset(
    {
        SignatureAlgorithmOID.RSA_WITH_MD5,
        SignatureAlgorithmOID.RSA_WITH_SHA1,
        x509.ObjectIdentifier("1.3.14.3.2.29"),  # sha1WithRSASignature, OIW's older name for it
        SignatureAlgorithmOID.ECDSA_WITH_SHA1,
        SignatureAlgorithmOID.DSA_WITH_SHA1,
    }
)
"""The algorithms an issuer may not have signed a certificate with: SHA-1's and MD5's."""


def _is_authority(certificate: x509.Certificate) -> bool:
    try:
        return certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


def is_its_key(certificate: x509.Certificate, key: rsa.RSAPublicKey) -> bool:
    """Whether ``key`` is the key ``certificate`` names."""
    own = certificate.public_key()
    return isinstance(own, rsa.RSAPublicKey) and own.public_numbers() == key.public_numbers()


def facts_of(certificate: x509.Certificate, *, matches_key: bool) -> CertificateFacts:
    """The facts of ``certificate``; ``matches_key`` says whether it is the key's (or the entity's)."""
    public = certificate.public_key()
    return CertificateFacts(
        der=certificate.public_bytes(serialization.Encoding.DER),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()),
        serial_number=format(certificate.serial_number, "x"),
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        rsa_bits=public.key_size if isinstance(public, rsa.RSAPublicKey) else None,
        may_sign=dsig.signing_problem(certificate) is None
        and certificate.signature_algorithm_oid not in BROKEN_SIGNATURES,
        is_authority=_is_authority(certificate),
        matches_key=matches_key,
    )
