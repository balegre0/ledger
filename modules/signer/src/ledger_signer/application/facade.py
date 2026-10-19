"""``SignerPort`` and ``SignerApi`` in the signer's own process, over its use cases (plan Decision 2).

The signer role binds this facade; every other role binds the gRPC client, which answers the same errors. The
use cases raise the signer's own codes (the domain's ``signer_window_not_covered``, ``signer_clock_skew``,
``certificate_retired``; fiscal-kit's ``xml_*``), which the gRPC server sends as they are: here, as in the
client, the port's contract turns them into fiscal-kit's errors (``port_error``, one mapping for both).
Anything else passes as it is: a defect (``signer_custody_damaged``) stays one. ``@facade`` refuses a
call made while the caller holds a unit; each call acts as its caller's actor for the audit.
"""

from typing import Final, final, override
lazy from collections.abc import Awaitable, Sequence

from ledger_fiscal_kit.ports import (
    CertificateValidity,
    SignatureRefusedError,
    SignerPort,
    SigningCertificateMissingError,
    SigningCertificateNotValidError,
    SigningClockSkewError,
)
from ledger_kernel.errors import ErrorKind, InvalidValueError, LedgerError
from ledger_kernel.tenancy import Tenant
from ledger_platform.runtime.calls import acting
from ledger_platform.runtime.facade import facade
from ledger_signer.api import SignerApi
from ledger_signer.domain.errors import CertificateRetiredError, ClockSkewError, NotCoveredError
lazy from ledger_fiscal_kit.ports import SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Mode
lazy from ledger_platform.runtime.calls import Call, SystemCall
lazy from ledger_signer.api import CertificateView
lazy from ledger_signer.application.certificates import Describing
lazy from ledger_signer.application.signing import Signing

IN_PROCESS: Final = "signer"
"""The caller an in-process signature is audited for: the signer's own process."""
_DOCUMENT_CODES: Final = ("xml_", "signature_")
"""The prefixes of the codes of a document that cannot be signed (fiscal-kit's ``xml`` and the port's)."""
_CUSTODY_CODES: Final = frozenset({"xml_signing_pair_refused", "xml_signing_key_not_rsa"})
"""``dsig``'s codes that mean custody is damaged: never the document's fault (``signing``)."""


def port_error(code: str, kind: ErrorKind) -> LedgerError | None:
    """The ``SignerPort`` error the signer's error ``code`` of ``kind`` is, or ``None`` when it is none.

    A document that cannot be signed is ``SignatureRefusedError`` with its code as the reason.
    """
    match code, kind:
        case NotCoveredError.code, ErrorKind.CONFLICT:
            return SigningCertificateNotValidError()
        case ClockSkewError.code, ErrorKind.CONFLICT:
            return SigningClockSkewError()
        case ((CertificateRetiredError.code | SigningCertificateMissingError.code), ErrorKind.CONFLICT):
            return SigningCertificateMissingError()
        case _, ErrorKind.INVALID if code.startswith(_DOCUMENT_CODES) and code not in _CUSTODY_CODES:
            return SignatureRefusedError(code)
        case _:
            return None


async def _as_port[R](signature: Awaitable[R]) -> R:
    """``signature``'s answer, its signer errors as the port's."""
    try:
        return await signature
    except LedgerError as error:
        answered = port_error(error.code, error.kind)
        if answered is None:
            raise
        raise answered from error


@final
class SignerFacade(SignerPort, SignerApi):
    """The port and the API, as the signer's own process implements them."""

    __slots__ = ("_describing", "_signing")

    def __init__(self, *, signing: Signing, describing: Describing) -> None:
        self._signing = signing
        self._describing = describing

    @override
    @facade
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        with acting(call):
            return await _as_port(self._signing.sign_xml(IN_PROCESS, request))

    @override
    @facade
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        with acting(call):
            return await _as_port(self._signing.sign_seed(IN_PROCESS, request))

    @override
    @facade
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        """The validity of the certificate serving ``tenant``: as it is written in it, both ends included."""
        view = await self._describing.active(tenant)
        if view is None:
            return None
        return CertificateValidity(certificate=view.id, not_before=view.not_before, not_after=view.not_after)

    @override
    @facade
    async def certificates(
        self, call: Call | SystemCall, company: CompanyId, mode: Mode
    ) -> Sequence[CertificateView]:
        scope = call.scope
        if scope is None:
            raise InvalidValueError(field="call.scope", requirement="an organization or company scope")
        tenant = Tenant(scope.organization, company, mode)
        return await self._describing.certificates(tenant, include_retired=False)
