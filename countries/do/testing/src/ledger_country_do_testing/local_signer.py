"""A signer that holds a key in this process: for tests, the fake DGII and the spike CLI, never production.

In production keys stay in the signer process (spec rule 7) and the channel signs through ``SignerPort``
(``adapters.signing.port.PortSigner``); this distribution is dev-only and never installed in an image. It
answers as the signer would: the pair is refused when the signer is built (as the signer refuses it at
import), a seed must be DGII's (``require_seed``) before the key sees it, and a document the profile refuses
is ``DocumentUnsignableError`` with the profile's code as its reason (the signer's ``signature_refused``).
Every signature goes through the port's request objects (``XmlToSign``, ``SeedToSign``, for ``TENANT``), so
the port's limits hold here too (an aware ``signed_at``, a plain reference, a host as audience). It checks no
validity: that rule is the signer's. A PKCS#12 bundle is read from bytes the caller loaded (the CLI
reads it from the git-ignored ``.dgii/`` directory); its password is never echoed, and the key is never
written, logged or shown in a ``repr``.
"""

import asyncio
from typing import ClassVar, Final, final, override
from uuid import UUID
lazy from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_country_do.channels.ecf_v1.adapters.signing.port import PROFILE
from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import require_seed
from ledger_country_do.channels.ecf_v1.application.ports import DocumentUnsignableError, XmlSigner
from ledger_fiscal_kit.ports import SeedToSign, XmlToSign
from ledger_fiscal_kit.xml.dsig import NotAnRsaKeyError, require_signing_pair, sign_enveloped
from ledger_fiscal_kit.xml.hardened import XmlDocumentError
from ledger_kernel.errors import InvalidInputError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_testing.pki import Issued

TENANT: Final = Tenant(OrganizationId(UUID(int=1)), CompanyId(UUID(int=2)), Mode.TEST)
"""Whom the test signer's requests name: they are built for their limits, and go nowhere."""


class BundleIncompleteError(InvalidInputError):
    """A PKCS#12 bundle without a private key and its certificate."""

    code: ClassVar[str] = "do_bundle_incomplete"

    def __init__(self) -> None:
        super().__init__("the PKCS#12 bundle holds no private key and certificate pair")


@final
class KeyPairSigner(XmlSigner):
    """``XmlSigner`` over a key and certificate in memory (see the module); signing runs in a worker thread.

    ``NotAnRsaKeyError`` or ``SigningPairError`` (fiscal-kit's) when the pair cannot sign together.
    """

    __slots__ = ("_certificate", "_key")

    def __init__(self, key: PrivateKeyTypes, certificate: x509.Certificate) -> None:
        if not isinstance(key, RSAPrivateKey):
            raise NotAnRsaKeyError
        require_signing_pair(key.public_key(), certificate)
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
    async def sign_document(self, document: bytes, *, signed_at: datetime, reference: str) -> bytes:
        request = XmlToSign(
            tenant=TENANT, profile=PROFILE, document=document, signed_at=signed_at, reference=reference
        )
        return await self._signed(request.document)

    @override
    async def sign_seed(self, seed: bytes, *, audience: str) -> bytes:
        require_seed(seed)
        request = SeedToSign(tenant=TENANT, profile=PROFILE, seed=seed, audience=audience)
        return await self._signed(request.seed)

    async def _signed(self, document: bytes) -> bytes:
        try:
            return await asyncio.to_thread(sign_enveloped, document, self._key, self._certificate)
        except XmlDocumentError as error:
            raise DocumentUnsignableError(error.code) from error
