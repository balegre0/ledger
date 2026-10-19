"""The channel's ``XmlSigner`` over fiscal-kit's ``SignerPort``, for one issuing company in one mode.

Each signature is a call of its own (``SystemCall``: the caller's reason, the tenant as its scope, a fresh
call id) carrying DGII's profile, the moment the document states and its e-NCF, or a seed and the host it came
from. The signer decides whether the certificate may sign then; the channel never asks first. A seed is
checked to be DGII's (``require_seed``) here, before it reaches the port: every path of the channel to
``SignerPort.sign_seed`` passes this one check, so a hostile receiver never gets another document signed as a
seed. The port's errors become the channel's, each chained to the port's:

- ``SigningCertificateMissingError`` → ``CertificateMissingError`` (conflict);
- ``SigningCertificateNotValidError`` → ``CertificateNotValidError`` (conflict);
- ``SigningClockSkewError`` → ``SigningSkewError`` (unavailable: signing again may succeed);
- ``SignerUnavailableError`` → ``SignerUnavailableError`` (unavailable);
- ``SignatureRefusedError`` → ``DocumentUnsignableError`` (invalid, the signer's reason kept).

A port client that cannot reach the signer may surface its transport's failure instead: the platform's
``RemoteUnavailableError`` (down, too slow, failing; the gRPC client's mapping) or a ``TimeoutError``. Both
are the signer being unavailable (``SignerUnavailableError``), so the emitter parks rather than fails. Any
other error (a ``RemoteDefectError`` or ``RemoteForbiddenError``: a request or a token of ours the signer
refuses) is a defect and passes unchanged; whoever sends a document treats such an error escaping
``Emitter.send`` as an unknown outcome and never sends the document again (the emission module's rule).

``reason`` names every call (the audit's actor is ``system:<reason>``), so it is code-shaped: never personal
data. Each call carries the current span's ``traceparent``, so the signer's spans join the caller's trace.
The certificate that signed (``Signed.certificate``) is dropped: the channel's archive takes it once it has
one (v2-M3b).
"""

import re
from typing import Final, final, override
lazy from collections.abc import Awaitable
lazy from datetime import datetime

from ledger_country_do.channels.ecf_v1.adapters.signing.seeds import require_seed
from ledger_country_do.channels.ecf_v1.application.emission import SigningSkewError
from ledger_country_do.channels.ecf_v1.application.ports import (
    CertificateMissingError,
    CertificateNotValidError,
    DocumentUnsignableError,
    SignerUnavailableError,
    XmlSigner,
)
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
from ledger_platform.grpc.client import RemoteUnavailableError
from ledger_platform.observability.tracing import current_traceparent
from ledger_platform.runtime.calls import SystemCall
lazy from ledger_fiscal_kit.ports import Signed, SignerPort
lazy from ledger_kernel.tenancy import Tenant

PROFILE: Final = SignatureProfile.XMLDSIG_ENVELOPED_RSA_SHA256
"""DGII's profile (*Firmado de e-CF*), the one the channel's verifier checks (``verifier.STRICT``)."""
_REASON: Final = re.compile(r"[a-z][a-z0-9._:-]{0,63}", re.ASCII)
_REASON_RULE: Final = "1 to 64 characters of a-z 0-9 . _ : -, a letter first"


@final
class PortSigner(XmlSigner):
    """``XmlSigner`` over ``SignerPort`` for ``tenant``; ``reason`` names the calls (see the module)."""

    __slots__ = ("_port", "_reason", "_tenant")

    def __init__(self, port: SignerPort, *, tenant: Tenant, reason: str) -> None:
        if tenant.mode is None:
            raise InvalidValueError(field="port_signer.tenant", requirement="a tenant in a mode")
        if _REASON.fullmatch(reason) is None:
            raise InvalidValueError(field="port_signer.reason", requirement=_REASON_RULE)
        self._port = port
        self._tenant = tenant
        self._reason = reason

    @override
    async def sign_document(self, document: bytes, *, signed_at: datetime, reference: str) -> bytes:
        request = XmlToSign(
            tenant=self._tenant, profile=PROFILE, document=document, signed_at=signed_at, reference=reference
        )
        return await _signed(self._port.sign_xml(self._call(), request))

    @override
    async def sign_seed(self, seed: bytes, *, audience: str) -> bytes:
        require_seed(seed)
        request = SeedToSign(tenant=self._tenant, profile=PROFILE, seed=seed, audience=audience)
        return await _signed(self._port.sign_seed(self._call(), request))

    def _call(self) -> SystemCall:
        return SystemCall(reason=self._reason, scope=self._tenant, traceparent=current_traceparent())


async def _signed(signing: Awaitable[Signed]) -> bytes:
    """What ``signing`` signed; the port's errors raised as the channel's (see the module)."""
    try:
        return (await signing).document
    except SigningCertificateMissingError as error:
        raise CertificateMissingError from error
    except SigningCertificateNotValidError as error:
        raise CertificateNotValidError from error
    except SigningClockSkewError as error:
        raise SigningSkewError from error
    except (PortUnavailableError, RemoteUnavailableError, TimeoutError) as error:
        raise SignerUnavailableError from error
    except SignatureRefusedError as error:
        raise DocumentUnsignableError(error.reason) from error
