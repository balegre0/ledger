"""The remote backend: the company's key at an INDOTEL-authorized certification entity (spec §3.7).

We hold the entity's credential, sealed exactly as a key is. Admitting asks the entity for the certificate it
signs with for the credential (the first of its chain) and reads its facts. Signing unwraps the credential
for this signature alone and sends the entity the SHA-256 digest of ``SignedInfo``, never the document (CSC
API v2 ``signatures/signHash``); the value it answers is checked against the certificate by ``dsig`` as the
document is completed, in a thread.

An answer that holds no certificate is ``EntityAnswerInvalidError``: the entity misbehaved (an internal
error, alerted), not the company.
"""

import asyncio
import hashlib
from typing import ClassVar, final

from cryptography import x509

from ledger_kernel.errors import LedgerError
from ledger_signer.adapters.custody.envelope import sealed, unsealed
from ledger_signer.adapters.custody.facts import facts_of
from ledger_signer.application.ports import Admitted
lazy from ledger_fiscal_kit.xml.dsig import Prepared
lazy from ledger_signer.application.ports import (
    CertificationEntity,
    KeyManagement,
    SealContext,
    SealedMaterial,
    Secret,
)


class EntityAnswerInvalidError(LedgerError):
    """The certification entity answered no certificate it signs with: a defect of the entity."""

    code: ClassVar[str] = "entity_answer_invalid"

    def __init__(self) -> None:
        super().__init__("the certification entity answered no certificate")


@final
class RemoteBackend:
    """Keys at a certification entity (see the module)."""

    __slots__ = ("_entity", "_kms")

    def __init__(self, kms: KeyManagement, entity: CertificationEntity) -> None:
        self._kms = kms
        self._entity = entity

    async def admit(self, credential: Secret, *, seal_for: SealContext) -> Admitted:
        """The facts of the certificate the entity signs with for ``credential``, and it sealed."""
        plain = credential.reveal().encode()
        chain = (await self._entity.certificate(plain)).chain
        if not chain:
            raise EntityAnswerInvalidError
        try:
            certificate = x509.load_der_x509_certificate(chain[0])
        except ValueError:
            raise EntityAnswerInvalidError from None
        facts = facts_of(certificate, matches_key=True)  # the entity's certificate is the key's by definition
        return Admitted(facts=facts, material=await sealed(self._kms, plain, seal_for))

    async def sign(
        self, material: SealedMaterial, *, prepared: Prepared, certificate_der: bytes, seal_for: SealContext
    ) -> bytes:
        """``prepared``'s document signed by the entity with the credential ``material`` seals."""
        credential = await unsealed(self._kms, material, seal_for)
        value = await self._entity.sign_digest(credential, hashlib.sha256(prepared.to_sign).digest())
        return await asyncio.to_thread(
            prepared.complete, value, x509.load_der_x509_certificate(certificate_der)
        )
