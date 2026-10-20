"""Opening a company's PKCS#12 bundle: its RSA key and the certificate it signs with.

Every bundle that does not open, whatever the reason (a wrong password, bytes that are no bundle, a bundle cut
short, one over ``MAX_BUNDLE_BYTES``, refused unread), is ``BundleUnreadableError``, raised from nothing: the
library's message may say which reason it was, and so help guess a password. A bundle that opens without an
RSA key or without its certificate is ``BundleIncompleteError``. Opening is CPU work (the password's key
derivation): custody calls it in a thread.
"""

from typing import Final

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
lazy from cryptography import x509

from ledger_signer.application.ports import BundleIncompleteError, BundleUnreadableError
lazy from ledger_signer.application.ports import Secret

MAX_BUNDLE_BYTES: Final = 2 * 1024 * 1024
"""The largest bundle custody opens (2 MiB: a company's bundle is a few KiB; the import refuses more)."""


def open_bundle(bundle: bytes, password: Secret) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """The RSA key and the certificate of ``bundle``, opened with ``password`` (see the module)."""
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise BundleUnreadableError
    try:
        opened = pkcs12.load_pkcs12(bundle, password.reveal().encode())
    except ValueError, UnsupportedAlgorithm:  # an algorithm the library lacks is a bundle it cannot open
        raise BundleUnreadableError from None
    key, certificate = opened.key, opened.cert
    if not isinstance(key, rsa.RSAPrivateKey) or certificate is None:
        raise BundleIncompleteError
    return key, certificate.certificate
