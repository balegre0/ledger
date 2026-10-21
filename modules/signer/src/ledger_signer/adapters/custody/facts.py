"""What custody reads of a certificate for the domain to decide on (``CertificateFacts``): no secret in it.

``may_sign`` is fiscal-kit's rule of what may sign (``dsig.signing_problem``: no authority, a key usage that
allows a signature), and one more: the certificate's issuer signed it with a SHA-2 hash (RSA PKCS#1 v1.5 or
ECDSA over SHA-224 to SHA-512, or RSASSA-PSS over one of them). Anything else (SHA-1, MD5, MD4, MD2, PSS over
another hash, an algorithm the library does not know) is refused at the import as a certificate that may not
sign (``certificate_not_for_signing``): a weak hash lets whoever forges a collision make a certificate that
names anyone.

This is a deliberate stopgap, decided at the review of v2-M2b Task 10: it looks at the one certificate only.
Validating its path to an accepted certification authority (each signature in the chain, its algorithms,
revocation) is deferred (spec §10, v2-M5); this rule goes when that check exists (see Task 15's notes).
"""

from typing import Final

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import SignatureAlgorithmOID

from ledger_fiscal_kit.xml import dsig
from ledger_signer.domain.certificates import CertificateFacts

SHA2_SIGNATURES: Final = frozenset(
    {
        SignatureAlgorithmOID.RSA_WITH_SHA224,
        SignatureAlgorithmOID.RSA_WITH_SHA256,
        SignatureAlgorithmOID.RSA_WITH_SHA384,
        SignatureAlgorithmOID.RSA_WITH_SHA512,
        SignatureAlgorithmOID.ECDSA_WITH_SHA224,
        SignatureAlgorithmOID.ECDSA_WITH_SHA256,
        SignatureAlgorithmOID.ECDSA_WITH_SHA384,
        SignatureAlgorithmOID.ECDSA_WITH_SHA512,
    }
)
"""The algorithms an issuer may have signed a certificate with, besides RSASSA-PSS over a SHA-2 hash."""
_SHA2: Final = (hashes.SHA224, hashes.SHA256, hashes.SHA384, hashes.SHA512)


def _signed_with_sha2(certificate: x509.Certificate) -> bool:
    """Whether ``certificate``'s issuer signed it with a SHA-2 hash (see the module: a stopgap)."""
    algorithm = certificate.signature_algorithm_oid
    if algorithm != SignatureAlgorithmOID.RSASSA_PSS:
        return algorithm in SHA2_SIGNATURES
    try:
        return isinstance(certificate.signature_hash_algorithm, _SHA2)
    except UnsupportedAlgorithm:  # a hash the library does not know is no SHA-2 hash
        return False


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
        may_sign=dsig.signing_problem(certificate) is None and _signed_with_sha2(certificate),
        is_authority=_is_authority(certificate),
        matches_key=matches_key,
    )
