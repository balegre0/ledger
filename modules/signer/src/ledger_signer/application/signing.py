"""Signatures: made outside any unit, bounded by the signing pool, answered once audited (spec §3.7).

For each signature, in this order: unit 1 reads the active certificate serving the company in the request's
mode and its sealed secret (``SigningCertificateMissingError`` without one); the certificate must cover the
moment the document states and the skew after it, and that moment must be within the skew of the signer's
clock (``require_signable``; a seed states the signer's own time); inside the signing pool (at most
``concurrency`` at once) fiscal-kit's ``dsig`` prepares the document in a thread and custody signs it, which
unwraps the data key through the KMS every time (Decision 18) or asks the entity; then unit 2 records the
audit row, and only once it has committed is the signature answered (Review Focus 6). A certificate retired
between the two units still audits the signature it made: it was valid when it signed.

A key that does not sign for its certificate (``dsig``'s ``xml_signing_pair_refused`` or
``xml_signing_key_not_rsa``) means custody is damaged, never that the document is wrong: it is an internal
error of its own (``CustodyDamagedError``) before it can cross a boundary. The audit row names the caller, the
mode, the profile, the document's SHA-256, its reference (or a seed's audience) and the backend, never the
document.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import ClassVar, Final, final
lazy from collections.abc import Callable, Mapping
lazy from datetime import datetime

from ledger_fiscal_kit.ports import SignatureProfile, Signed, SigningCertificateMissingError
from ledger_fiscal_kit.xml import dsig
from ledger_fiscal_kit.xml.hardened import XmlDocumentError
from ledger_kernel.errors import LedgerError
from ledger_signer.application.certificates import require_mode
from ledger_signer.application.ports import SealContext
lazy from ledger_fiscal_kit.ports import SeedToSign, XmlToSign
lazy from ledger_kernel.clock import Clock
lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_signer.application.ports import AuditAction, Custody, SignerRules, SignerUnits

_PREPARE: Final[Mapping[SignatureProfile, Callable[[bytes], dsig.Prepared]]] = {
    SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256: dsig.prepare,
}
"""How each profile prepares a document: phase one of its signature."""


def _prepared(profile: SignatureProfile, document: bytes) -> dsig.Prepared:
    """Phase one of ``profile``'s signature of ``document``; a refusal carries no parser's message.

    A parser's error may quote the document (lxml names the element it choked on): it is dropped from the
    refusal's chain, so it reaches no log, span or answer.
    """
    try:
        return _PREPARE[profile](document)
    except XmlDocumentError as error:
        error.__context__ = None
        raise error from None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _Signature:
    """A signature to make: what it audits as, for whom, in which profile, of what, at which moment."""

    action: AuditAction
    tenant: Tenant
    profile: SignatureProfile
    document: bytes = field(repr=False)
    signed_at: datetime
    named: Mapping[str, str]
    """What the audit row adds: the document's reference, or the seed's audience."""


class CustodyDamagedError(LedgerError):
    """A key in custody that does not sign for its certificate: custody is damaged (an internal error)."""

    code: ClassVar[str] = "signer_custody_damaged"

    def __init__(self) -> None:
        super().__init__("the key in custody does not sign for its certificate")


@final
class Signing:
    """Documents and seeds signed with the certificate serving a company in a mode (see the module)."""

    __slots__ = ("_clock", "_custody", "_pool", "_rules", "_units")

    def __init__(self, *, units: SignerUnits, custody: Custody, rules: SignerRules, clock: Clock) -> None:
        self._units = units
        self._custody = custody
        self._rules = rules
        self._clock = clock
        self._pool = asyncio.Semaphore(rules.concurrency)

    async def sign_xml(self, caller: str, request: XmlToSign) -> Signed:
        """``request.document`` signed, its signature audited for ``caller`` with its reference."""
        signature = _Signature(
            action="certificate.sign_xml",
            tenant=request.tenant,
            profile=request.profile,
            document=request.document,
            signed_at=request.signed_at,
            named={"reference": request.reference},
        )
        return await self._sign(caller, signature)

    async def sign_seed(self, caller: str, request: SeedToSign) -> Signed:
        """``request.seed`` signed at the signer's time, audited for ``caller`` with its audience."""
        signature = _Signature(
            action="certificate.sign_seed",
            tenant=request.tenant,
            profile=request.profile,
            document=request.seed,
            signed_at=self._clock(),
            named={"audience": request.audience},
        )
        return await self._sign(caller, signature)

    async def _sign(self, caller: str, signature: _Signature) -> Signed:
        tenant, document = signature.tenant, signature.document
        mode = require_mode("signing", tenant)
        async with self._units.unit(tenant) as unit:
            certificate = await unit.certificates.active(tenant.company, mode)
            if certificate is None:
                raise SigningCertificateMissingError
            material = await unit.certificates.material(certificate)
        certificate.require_signable(signed_at=signature.signed_at, now=self._clock(), skew=self._rules.skew)
        seal_for = SealContext(
            organization=tenant.organization, company=tenant.company, certificate=certificate.id
        )
        async with self._pool:
            prepared = await asyncio.to_thread(_prepared, signature.profile, document)
            try:
                signed = await self._custody.sign(
                    material,
                    backend=certificate.backend,
                    prepared=prepared,
                    certificate_der=certificate.der,
                    seal_for=seal_for,
                )
            except (dsig.SigningPairError, dsig.NotAnRsaKeyError) as error:
                raise CustodyDamagedError from error
        async with self._units.unit(tenant) as unit:
            unit.audit.record(
                signature.action,
                certificate.id,
                detail={
                    "caller": caller,
                    "mode": mode.value,
                    "profile": signature.profile.value,
                    "document_sha256": hashlib.sha256(document).hexdigest(),
                    **signature.named,
                    "backend": certificate.backend.value,
                },
            )
        return Signed(document=signed, certificate=certificate.id)
