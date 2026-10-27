"""A signer that holds a key in this process: for tests and the fake DGII, never production.

In production keys stay in the signer process (spec rule 7) and the channel signs through ``SignerPort``
(``adapters.signing.port.DocumentSigner``); this distribution is dev-only and never installed in an image.
``KeyPairSigner`` is a ``SignerPort`` over a key and certificate in memory, answering as the signer would: the
pair is refused when it is built (as the signer refuses it at import), and a document the profile refuses is
``SignatureRefusedError`` with the profile's code as its reason. It checks no validity, no seed's shape (the
channel's ``DocumentSigner`` does, before the port) and no clock: those rules are the signer's.
``sign`` signs bytes directly, for a test that needs a signed document without a port. The key is never
written, logged or shown in a ``repr``.

``SIGNING_AS`` is whom the tests sign and call DGII as: a company of ``TENANT``, in test mode.
"""

import asyncio
import hashlib
from datetime import UTC
from typing import Final, final, override
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
lazy from cryptography import x509
lazy from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from ledger_country_do.channels.ecf_v1.application.ports import SigningAs
from ledger_fiscal_kit.ports import CertificateValidity, SignatureRefusedError, Signed, SignerPort
from ledger_fiscal_kit.xml.dsig import NotAnRsaKeyError, require_signing_pair, sign_enveloped
from ledger_fiscal_kit.xml.hardened import XmlDocumentError
from ledger_kernel.ids import CompanyId, OrganizationId
from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_fiscal_kit.ports import SeedToSign, XmlToSign
lazy from ledger_platform.runtime.calls import SystemCall
lazy from ledger_testing.pki import Issued

TENANT: Final = Tenant(OrganizationId(UUID(int=1)), CompanyId(UUID(int=2)), Mode.TEST)
"""The company in test mode the tests sign as."""
SIGNING_AS: Final = SigningAs(
    organization=TENANT.organization, company=TENANT.company, mode=Mode.TEST, reason="do-ecf-v1.local"
)
"""``TENANT``'s company, as the channel names whom it signs and calls DGII for."""


@final
class KeyPairSigner(SignerPort):
    """``SignerPort`` over a key and certificate in memory (see the module); signing runs in a worker thread.

    ``NotAnRsaKeyError`` or ``SigningPairError`` (fiscal-kit's) when the pair cannot sign together.
    """

    __slots__ = ("_certificate", "_id", "_key")

    def __init__(self, key: PrivateKeyTypes, certificate: x509.Certificate) -> None:
        if not isinstance(key, RSAPrivateKey):
            raise NotAnRsaKeyError
        require_signing_pair(key.public_key(), certificate)
        self._key = key
        self._certificate = certificate
        self._id = UUID(bytes=hashlib.sha256(certificate.tbs_certificate_bytes).digest()[:16])

    @classmethod
    def from_issued(cls, issued: Issued) -> KeyPairSigner:
        """A signer of a test certificate."""
        return cls(issued.key, issued.certificate)

    @property
    def certificate(self) -> x509.Certificate:
        """The certificate the signatures carry."""
        return self._certificate

    @property
    def certificate_id(self) -> UUID:
        """The certificate's id as this signer answers it (``Signed.certificate``): derived from it."""
        return self._id

    def sign(self, document: bytes) -> bytes:
        """``document`` signed in DGII's profile; fiscal-kit's ``XmlDocumentError`` when it refuses it."""
        return sign_enveloped(document, self._key, self._certificate)

    @override
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        del call
        return await self._signed(request.document)

    @override
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        del call
        return await self._signed(request.seed)

    @override
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        del call, tenant
        return CertificateValidity(
            certificate=self._id,
            not_before=self._certificate.not_valid_before_utc.astimezone(UTC),
            not_after=self._certificate.not_valid_after_utc.astimezone(UTC),
        )

    async def _signed(self, document: bytes) -> Signed:
        try:
            signed = await asyncio.to_thread(self.sign, document)
        except XmlDocumentError as error:
            raise SignatureRefusedError(error.code) from error
        return Signed(document=signed, certificate=self._id)
