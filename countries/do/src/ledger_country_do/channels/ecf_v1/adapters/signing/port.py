"""The channel's ``Signatures`` over fiscal-kit's ``SignerPort``: ``DocumentSigner``, for any issuing company.

Each signature is a call of its own (``SystemCall``: the reason ``SigningAs`` names, the issuing company in
its mode as its scope, a fresh call id) carrying DGII's profile, the moment the document states and its
e-NCF, or a seed and the host it came from. The signer decides whether the certificate may sign then; the
channel never asks first. A seed is checked to be DGII's (``require_seed``) here, before it reaches the
port: every path of the channel to ``SignerPort.sign_seed`` passes this one check, so a hostile receiver
never gets another document signed as a seed. The port's errors become the channel's, each chained to the
port's:

- ``SigningCertificateMissingError`` → ``CertificateMissingError`` (conflict: install one);
- ``SigningCertificateNotValidError`` → ``CertificateNotValidError`` (conflict: renew, or wait);
- ``SigningClockSkewError`` → ``SignerClockSkewError`` (conflict: a clock to fix; a new moment from the
  same clock is refused again);
- ``SignerUnavailableError`` → ``SignerUnavailableError`` (unavailable: retry later, nothing was sent);
- ``SignatureRefusedError`` → ``DocumentUnsignableError`` (invalid, the signer's reason kept).

A port client that cannot reach the signer may surface its transport's failure instead: the platform's
``RemoteUnavailableError`` (down, too slow, failing; the gRPC client's mapping) or a ``TimeoutError``. Both
are the signer being unavailable (``SignerUnavailableError``), so a document parks rather than fails. Any
other error (a ``RemoteDefectError`` or ``RemoteForbiddenError``: a request or a token of ours the signer
refuses) is a defect and passes unchanged; a run that escapes with one after marking its submission
leaves the outcome unknown, and the document is never sent again (``application.advancing``).

A document comes back with the certificate that signed it (``SignedDocument.certificate``, the signer's own
id), for the archive: never recovered from the signature. Each call carries the current span's
``traceparent``, so the signer's spans join the caller's trace.
"""

from typing import Final, final, override
lazy from collections.abc import Awaitable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import require_seed
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    DocumentUnsignableError,
    Signatures,
    SignedDocument,
    SignerClockSkewError,
    SignerUnavailableError,
    SigningAs,
)
from ledger_country_do.channels.ecf_v1.application.requirements import Certificates
from ledger_fiscal_kit.ports import (
    SeedToSign,
    SignatureProfile,
    SignatureRefusedError,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
    XmlToSign,
)
from ledger_fiscal_kit.ports import SignerUnavailableError as PortUnavailableError
from ledger_kernel.errors import InvalidValueError
from ledger_kernel.tenancy import Tenant
from ledger_platform.grpc.client import RemoteUnavailableError
from ledger_platform.observability.tracing import current_traceparent
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_fiscal_kit.ports import CertificateValidity, Signed, SignerPort

PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
"""DGII's profile (*Firmado de e-CF*), the one the channel's verifier checks (``verifier.STRICT``)."""


def signing_as(call: SystemCall) -> SigningAs:
    """Whom ``call`` acts for, as the channel signs: its scope (a company in a mode) and its reason.

    A job or consumer that works for one issuing company passes its own call; one whose scope is not a
    company in a mode is a defect of its caller (``InvalidValueError``).
    """
    scope = call.scope
    if not isinstance(scope, Tenant) or scope.mode is None:
        raise InvalidValueError(field="signing_as.call", requirement="a call scoped to a company in a mode")
    return SigningAs(
        organization=scope.organization, company=scope.company, mode=scope.mode, reason=call.reason
    )


@final
class DocumentSigner(Signatures, Certificates):
    """``Signatures`` over ``SignerPort`` (see the module), for any issuing company the call names.

    It is ``Certificates`` too: whether a company's certificate covers a moment (what enrollments require).
    """

    __slots__ = ("_port",)

    def __init__(self, port: SignerPort) -> None:
        self._port = port

    @override
    async def document(
        self, who: SigningAs, document: bytes, *, signed_at: datetime, reference: str
    ) -> SignedDocument:
        request = XmlToSign(
            tenant=who.tenant, profile=PROFILE, document=document, signed_at=signed_at, reference=reference
        )
        signed = await _signed(self._port.sign_xml(_call(who), request))
        return SignedDocument(document=signed.document, certificate=signed.certificate)

    @override
    async def seed(self, who: SigningAs, seed: bytes, *, audience: str) -> bytes:
        require_seed(seed)
        request = SeedToSign(tenant=who.tenant, profile=PROFILE, seed=seed, audience=audience)
        return (await _signed(self._port.sign_seed(_call(who), request))).document

    async def validity(self, who: SigningAs) -> CertificateValidity | None:
        """When the certificate that signs for ``who`` may sign; ``None`` without one.

        ``SignerUnavailableError`` when the signer cannot be reached; nothing else of the port's is mapped.
        """
        try:
            return await self._port.certificate_validity(_call(who), who.tenant)
        except (PortUnavailableError, RemoteUnavailableError, TimeoutError) as error:
            raise SignerUnavailableError from error

    @override
    async def valid_at(self, who: SigningAs, moment: datetime) -> bool:
        validity = await self.validity(who)
        return validity is not None and validity.covers(moment)


def _call(who: SigningAs) -> SystemCall:
    return SystemCall(reason=who.reason, scope=who.tenant, traceparent=current_traceparent())


async def _signed(signing: Awaitable[Signed]) -> Signed:
    """What ``signing`` signed; the port's errors raised as the channel's (see the module)."""
    try:
        return await signing
    except SigningCertificateMissingError as error:
        raise CertificateMissingError from error
    except SigningCertificateNotValidError as error:
        raise CertificateNotValidError from error
    except SigningClockSkewError as error:
        raise SignerClockSkewError from error
    except (PortUnavailableError, RemoteUnavailableError, TimeoutError) as error:
        raise SignerUnavailableError from error
    except SignatureRefusedError as error:
        raise DocumentUnsignableError(error.reason) from error
