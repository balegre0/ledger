"""The signer, where none can be reached: every call answers ``signer_unavailable`` (Decision 13).

What an app binds as ``SignerPort`` and ``SignerApi`` while it has no client of the signer, and what the
routes of ``api`` ask (``CertificateService``): a client role without the signer's address or its own token,
and every role but the client roles and the signer's. A route then answers 503 and a channel parks; nothing
fails at the start, and no app's readiness depends on the signer (spec §3.7).
"""

from typing import final, override
lazy from collections.abc import AsyncIterable, Sequence

from ledger_fiscal_kit.ports import SignerPort, SignerUnavailableError
from ledger_platform.runtime.facade import facade
from ledger_signer.api import SignerApi
from ledger_signer.application.service import CertificateService
lazy from ledger_fiscal_kit.ports import CertificateValidity, SeedToSign, Signed, XmlToSign
lazy from ledger_kernel.ids import CompanyId
lazy from ledger_kernel.tenancy import Mode, Tenant
lazy from ledger_platform.runtime.calls import Call, SystemCall
lazy from ledger_signer.api import CertificateId, CertificateView
lazy from ledger_signer.application.importing import ImportRequest
lazy from ledger_signer.application.ports import ImportOutcome, Secret


@final
class UnavailableSigner(SignerPort, SignerApi, CertificateService):
    """``SignerPort``, ``SignerApi`` and ``CertificateService``: ``SignerUnavailableError`` to every call."""

    __slots__ = ()

    @override
    @facade
    async def sign_xml(self, call: SystemCall, request: XmlToSign) -> Signed:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def sign_seed(self, call: SystemCall, request: SeedToSign) -> Signed:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def certificate_validity(self, call: SystemCall, tenant: Tenant) -> CertificateValidity | None:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def certificates(
        self, call: Call | SystemCall, company: CompanyId, mode: Mode
    ) -> Sequence[CertificateView]:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def describe(
        self, call: Call | SystemCall, tenant: Tenant, *, include_retired: bool
    ) -> tuple[CertificateView, ...]:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def retire(
        self, call: Call | SystemCall, company: Tenant, certificate: CertificateId
    ) -> CertificateView:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError

    @override
    @facade
    async def import_local(
        self, call: Call | SystemCall, request: ImportRequest, password: Secret, bundle: AsyncIterable[bytes]
    ) -> ImportOutcome:
        """Never: the signer is unavailable (the bundle is left unread)."""
        raise SignerUnavailableError

    @override
    @facade
    async def import_remote(
        self, call: Call | SystemCall, request: ImportRequest, credential: Secret
    ) -> ImportOutcome:
        """Never: the signer is unavailable."""
        raise SignerUnavailableError
