"""What the routes ask of the signer: in ``api`` its gRPC client, or the stand-in that it is unavailable.

The routes never open a bundle, nor hold a key: they forward what a request carries and show what the signer
answers. Each method raises what the signer's own process would (``client.API_REFUSALS``), fiscal-kit's
``SignerUnavailableError`` when it cannot be reached, and the ``RemoteError`` family for any other failure.
"""

from abc import ABCMeta, abstractmethod
from typing import Protocol
lazy from collections.abc import AsyncIterable

lazy from ledger_kernel.tenancy import Tenant
lazy from ledger_platform.runtime.calls import Call, SystemCall
lazy from ledger_signer.api import CertificateId, CertificateView
lazy from ledger_signer.application.importing import ImportRequest
lazy from ledger_signer.application.ports import ImportOutcome, Secret


class CertificateService(Protocol, metaclass=ABCMeta):
    """A company's certificates: listed, retired and imported, by the signer.

    The methods are abstract so that they need no body: a ``...`` is a statement no test could reach.
    """

    @abstractmethod
    async def describe(
        self, call: Call | SystemCall, tenant: Tenant, *, include_retired: bool
    ) -> tuple[CertificateView, ...]:
        """``tenant``'s certificates in its mode, active first; retired ones too if ``include_retired``."""

    @abstractmethod
    async def retire(
        self, call: Call | SystemCall, company: Tenant, certificate: CertificateId
    ) -> CertificateView:
        """``certificate`` of ``company`` retired for every mode, or as it is if it was already."""

    @abstractmethod
    async def import_local(
        self, call: Call | SystemCall, request: ImportRequest, password: Secret, bundle: AsyncIterable[bytes]
    ) -> ImportOutcome:
        """Install the certificate of the PKCS#12 ``bundle`` (streamed) opened with ``password``."""

    @abstractmethod
    async def import_remote(
        self, call: Call | SystemCall, request: ImportRequest, credential: Secret
    ) -> ImportOutcome:
        """Install the certificate an entity signs with for ``credential``."""
