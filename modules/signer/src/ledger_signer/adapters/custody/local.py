"""The local backend: the company's key in our custody, sealed under a data key the KMS wraps (spec §3.7).

Admitting a bundle opens it, reads its certificate's facts and seals its key (PKCS#8, DER) for the
certificate. Signing unwraps the data key for this signature alone, opens the key, signs ``SignedInfo``
(RSASSA-PKCS1-v1_5, SHA-256) and completes the document, which ``dsig`` checks against the certificate. The
CPU work (the bundle's key derivation, loading and using the key, completing the document) runs in a thread,
never on the event loop; the use case bounds how many at once.

Sealed material that is not a private key (a credential sealed for the remote backend) does not open
(``CustodyTamperedError``); a private key that is no RSA key is ``dsig.NotAnRsaKeyError`` raised from nothing:
custody is damaged either way, as the signing use case says.
"""

import asyncio
from typing import final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ledger_fiscal_kit.xml import dsig
from ledger_signer.adapters.custody.envelope import PRIVATE_KEY, sealed, unsealed
from ledger_signer.adapters.custody.facts import facts_of, is_its_key
from ledger_signer.adapters.custody.pkcs12 import open_bundle
from ledger_signer.application.ports import Admitted
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_signer.application.ports import KeyManagement, SealContext, SealedMaterial, Secret
lazy from ledger_signer.domain.certificates import CertificateFacts


def _opened(bundle: bytes, password: Secret) -> tuple[bytes, CertificateFacts]:
    """The key of ``bundle`` as PKCS#8 DER, and its certificate's facts."""
    key, certificate = open_bundle(bundle, password)
    plain = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return plain, facts_of(certificate, matches_key=is_its_key(certificate, key.public_key()))


def _signed(key_der: bytes, prepared: Prepared, certificate_der: bytes) -> bytes:
    """``prepared`` signed with the PKCS#8 key ``key_der`` and completed with the certificate."""
    try:
        key = serialization.load_der_private_key(key_der, password=None)
    except ValueError:
        raise dsig.NotAnRsaKeyError from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise dsig.NotAnRsaKeyError
    value = key.sign(prepared.to_sign, padding.PKCS1v15(), hashes.SHA256())
    return prepared.complete(value, x509.load_der_x509_certificate(certificate_der))


@final
class LocalBackend:
    """Keys in custody (see the module)."""

    __slots__ = ("_kms",)

    def __init__(self, kms: KeyManagement) -> None:
        self._kms = kms

    async def admit(self, bundle: bytes, password: Secret, *, seal_for: SealContext) -> Admitted:
        """The facts of ``bundle``'s certificate, and its key sealed for ``seal_for``."""
        plain, facts = await asyncio.to_thread(_opened, bundle, password)
        return Admitted(facts=facts, material=await sealed(self._kms, plain, seal_for, kind=PRIVATE_KEY))

    async def sign(
        self, material: SealedMaterial, *, prepared: Prepared, certificate_der: bytes, seal_for: SealContext
    ) -> bytes:
        """``prepared``'s document signed with the key ``material`` seals."""
        key_der = await unsealed(self._kms, material, seal_for, kind=PRIVATE_KEY)
        return await asyncio.to_thread(_signed, key_der, prepared, certificate_der)
