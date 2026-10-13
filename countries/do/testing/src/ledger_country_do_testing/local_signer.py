"""A signer that holds a key in this process: for tests, the fake DGII and the spike CLI, never production.

In production keys stay in the signer process (spec rule 7); this distribution is dev-only and never installed
in an image. A PKCS#12 bundle is read from bytes the caller loaded (the CLI reads it from the git-ignored
``.dgii/`` directory); its password is never echoed, and the key is never written, logged or shown in a
``repr``.
"""

import asyncio
from typing import final, override

from cryptography.hazmat.primitives.serialization import pkcs12
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_country_do.channels.ecf_v1.adapters.signing.signer import sign_enveloped
from ledger_country_do.channels.ecf_v1.application.ports import XmlSigner
lazy from ledger_country_do_testing.pki import Issued


class BundleIncompleteError(ValueError):
    """A PKCS#12 bundle without a private key and its certificate."""

    def __init__(self) -> None:
        super().__init__("the PKCS#12 bundle holds no private key and certificate pair")


@final
class KeyPairSigner(XmlSigner):
    """``XmlSigner`` over a key and certificate in memory; signing runs in a worker thread."""

    __slots__ = ("_certificate", "_key")

    def __init__(self, key: PrivateKeyTypes, certificate: x509.Certificate) -> None:
        self._key = key
        self._certificate = certificate

    @classmethod
    def from_issued(cls, issued: Issued) -> KeyPairSigner:
        """A signer of a test certificate."""
        return cls(issued.key, issued.certificate)

    @classmethod
    def from_pkcs12(cls, data: bytes, password: bytes) -> KeyPairSigner:
        """A signer of the key and certificate in a PKCS#12 bundle; ``ValueError`` on a wrong password."""
        loaded = pkcs12.load_pkcs12(data, password)
        if loaded.key is None or loaded.cert is None:
            raise BundleIncompleteError
        return cls(loaded.key, loaded.cert.certificate)

    @property
    def certificate(self) -> x509.Certificate:
        """The certificate the signatures carry."""
        return self._certificate

    @override
    async def sign_document(self, document: bytes) -> bytes:
        return await asyncio.to_thread(sign_enveloped, document, self._key, self._certificate)

    @override
    async def sign_seed(self, seed: bytes) -> bytes:
        return await asyncio.to_thread(sign_enveloped, seed, self._key, self._certificate, seed=True)
