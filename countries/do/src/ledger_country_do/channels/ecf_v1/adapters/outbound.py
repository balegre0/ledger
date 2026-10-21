"""The worker's clients of DGII and of buyers, opened when the app starts and closed when it stops.

Built inside the open block (integration checklist 5): the trusted roots are read then (a PEM file, read in a
thread), the DGII client and the buyer client made over the channel's ``OutboundHttp`` and opened, the
buyers' directory answered by DGII's through a cache (Decision 18). Assembly only names the file: an app
whose roots cannot be read does not start (``TrustedRootsUnreadableError``, which never names the file).
Outside the block the clients are not there (``OutboundClosedError``): a job that reaches them then is a
defect.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar, final
lazy from collections.abc import AsyncGenerator, Callable
lazy from datetime import datetime
lazy from pathlib import Path

from cryptography import x509

from ledger_country_do.channels.ecf_v1.adapters.buyers.client import BuyerClient
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiClient
from ledger_country_do.channels.ecf_v1.adapters.dgii.directory import DirectoryCache
from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
from ledger_country_do.channels.ecf_v1.domain.errors import EcfError
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
lazy from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.port import DocumentSigner
lazy from ledger_country_do.channels.ecf_v1.application.ports import Authority
lazy from ledger_platform.http.outbound import OutboundHttp


class TrustedRootsUnreadableError(EcfError):
    """``LEDGER_DO_ECF_TRUSTED_ROOTS`` cannot be read, or holds no certificate in PEM: the worker stops."""

    code: ClassVar[str] = "do_ecf_trusted_roots_unreadable"

    def __init__(self) -> None:
        super().__init__(
            "LEDGER_DO_ECF_TRUSTED_ROOTS must name a readable PEM file of one certificate or more"
        )


class OutboundClosedError(EcfError):
    """The clients were reached while the app is not running: a defect of what reached them."""

    code: ClassVar[str] = "do_ecf_outbound_closed"

    def __init__(self) -> None:
        super().__init__("DGII's and buyers' clients are open only while the app runs")


def unopened() -> Authority:
    """DGII's client of an app that has none (every role but the worker's): reaching it is a defect."""
    raise OutboundClosedError


def _read_roots(path: Path) -> tuple[x509.Certificate, ...]:
    """The certificates of ``path``, in PEM (``TrustedRootsUnreadableError`` without any)."""
    try:
        roots = x509.load_pem_x509_certificates(path.read_bytes())
    except OSError, ValueError:
        raise TrustedRootsUnreadableError from None
    return tuple(roots)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DgiiAccess:
    """Where DGII is and how long it is waited for: its hosts, and the waits a client and its calls keep."""

    hosts: DgiiHosts
    timeouts: DgiiTimeouts


@final
class Outbound:
    """The DGII and buyer clients of the worker (see the module)."""

    __slots__ = ("_access", "_buyers", "_clock", "_dgii", "_http", "_roots", "_signer")

    def __init__(
        self,
        signer: DocumentSigner,
        *,
        http: OutboundHttp,
        access: DgiiAccess,
        roots: Path,
        clock: Callable[[], datetime],
    ) -> None:
        self._signer = signer
        self._http = http
        self._access = access
        self._roots = roots
        self._clock = clock
        self._dgii: DgiiClient | None = None
        self._buyers: BuyerClient | None = None

    @property
    def dgii(self) -> DgiiClient:
        """DGII's client, open."""
        if self._dgii is None:
            raise OutboundClosedError
        return self._dgii

    def authority(self) -> Authority:
        """DGII's client, open, as the use cases see it (``Advancing``'s ``authority``)."""
        return self.dgii

    @property
    def buyers(self) -> BuyerClient:
        """The buyers' client, open."""
        if self._buyers is None:
            raise OutboundClosedError
        return self._buyers

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[Outbound]:
        """The clients, made and open for the length of the block (see the module)."""
        trust = CounterpartyTrust(roots=await asyncio.to_thread(_read_roots, self._roots))
        access = self._access
        dgii = DgiiClient(
            self._signer, http=self._http, hosts=access.hosts, timeouts=access.timeouts, clock=self._clock
        )
        buyers = BuyerClient(
            self._signer,
            DirectoryCache(dgii.directory).entry,
            trust=trust,
            http=self._http,
            timeouts=access.timeouts,
            clock=self._clock,
        )
        async with AsyncExitStack() as stack:
            self._dgii = await stack.enter_async_context(dgii.opened())
            self._buyers = await stack.enter_async_context(buyers.opened())
            try:
                yield self
            finally:
                self._dgii = None
                self._buyers = None
