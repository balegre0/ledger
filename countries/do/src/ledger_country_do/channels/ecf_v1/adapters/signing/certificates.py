"""What makes a certificate one that signs DGII's documents: the rule the signer and the verifier share.

An end-entity certificate (not a certification authority) whose key usage allows signing: ``digitalSignature``
or ``nonRepudiation`` (``content_commitment``), the latter what qualified tax-procedure certificates assert. A
certificate without a key-usage extension is refused: DGII's authorized authorities always write one. Whether
the certificate is trusted (its chain, its validity, its subject) is the caller's policy, not this rule.
"""

from typing import Final

from cryptography import x509

CERTIFICATION_AUTHORITY: Final = "the signing certificate is a certification authority's"
NOT_FOR_SIGNING: Final = "the signing certificate's key usage does not allow signing"


def signing_problem(certificate: x509.Certificate) -> str | None:
    """Why ``certificate`` cannot sign DGII's documents, or ``None`` when it can."""
    try:
        if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return CERTIFICATION_AUTHORITY
    except x509.ExtensionNotFound:
        pass
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return NOT_FOR_SIGNING
    return None if usage.digital_signature or usage.content_commitment else NOT_FOR_SIGNING
