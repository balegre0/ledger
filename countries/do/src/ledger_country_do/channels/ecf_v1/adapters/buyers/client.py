"""Delivering an accepted e-CF to an electronic buyer (spec §8.6.5 steps 2-4; DT-EMI; DT-DGII's directory).

The buyer is looked up in DGII's directory; no entry means it is no electronic receiver, and delivery does not
apply (the client hands it the printed representation). The directory's URLs are the buyer's to write, so
each is checked before anything is sent, and every request goes to an address checked public, never to a name
resolved again (``endpoints``). They are bases to which DGII's standard paths are appended (F5). When the
buyer publishes an authentication URL, its seed is signed (``XmlSigner.sign_seed`` refuses anything but DGII's
``SemillaModel``, read hardened; its audience the endpoint's host and port) and exchanged for its token
(``validacioncertificado``: issuer to buyer, DGII is not in the middle). The signed e-CF is posted to its
reception (multipart ``xml``, named RNC + e-NCF), and the answer must be a **signed ARECF**:

1. its signature holding under the inbound profile (``verify_enveloped(profile=INBOUND)``);
2. read as every received document is (``xml.inbound.read_inbound_arecf``: hardened, bounded, valid for
   DGII's schema, its integers read as integers, its moment a real one);
3. naming this very document: issuer, buyer and e-NCF;
4. signed by a certificate the trust policy accepts (``CounterpartyTrust``): chained to the configured roots,
   valid when the ARECF says it acknowledged (``FechaHoraAcuseRecibo``, Santo Domingo's time) and at
   receipt here, those two moments close, its usage allowing it, and its subject naming the buyer's RNC or
   cédula. Revocation is not checked (a recorded gap).

``Estado`` 0 is received (and then carries no reason: one that does contradicts itself); 1 is not, with
DGII's reason code. Requests go through the platform's ``OutboundHttp`` client ``BUYERS_CLIENT`` (traced
and measured with the host and status only, keeping no connection): they follow no redirect, ignore the
environment's proxies, verify TLS for the buyer's name, accept a plain or single-gzip answer of at most
``MAX_ANSWER`` bytes read as it streams, and each has the per-attempt limit of ``DgiiTimeouts`` (``total``)
within the delivery's overall deadline (``call``). A request goes to the next checked address only when the
previous could not be connected to; nothing is otherwise retried here, and no connection is reused.

**Failures, one classification.** A buyer that could not be reached, failed, timed out, answered other than
200, or whose answer cannot be read as bytes (an encoding refused, over the size limit), or whose seed or
token does not read, is *unavailable* (``BuyerUnavailableError``: v2-M4 retries later). A buyer that answered
200 with a receipt that fails DGII's schema, the hardened reading, its signature, its contents (another
document; received with a reason), its date or the trust policy is *invalid*: retrying would get the same
receipt, so it is not retried. An endpoint in DGII's directory that may not be reached is invalid too.

**A signing oracle (recorded, not closed).** A buyer's seed has DGII's own format (``SemillaModel``), and the
issuer signs whatever seed a buyer's authentication endpoint serves. A hostile endpoint can relay a seed it
fetched from DGII's ``autenticacion`` for this issuer's RNC, have the issuer sign it, and exchange the signed
seed for a DGII token as this issuer. Nothing in the seed binds it to its audience. So every buyer seed signed
is audit-logged (``SeedSigned``: issuer, buyer, the endpoint's host, the moment; never the seed, the
signature or a token), the question goes to DGII (runbook step 7, spike findings), and v2-M4 makes buyer
authentication a per-company switch, off unless a buyer requires it.

The receipt is the caller's to archive; the ``delivering`` state machine and its retries are v2-M4's.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, Required, TypedDict, Unpack, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError
lazy from cryptography import x509

from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import endpoint, pinned, system_resolve
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import TokenAnswer
from ledger_country_do.channels.ecf_v1.adapters.http import (
    ACCEPTED_ENCODING,
    BUYERS_CLIENT,
    DEFAULT_TIMEOUTS,
    bounded,
)
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.inbound import read_inbound_arecf
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError, EcfUnavailableError
from ledger_kernel.tax_ids import masked
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Endpoint, Resolve
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DirectoryEntry
lazy from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
lazy from ledger_country_do.channels.ecf_v1.application.ports import XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason
lazy from ledger_platform.http.outbound import OutboundHttp

RECEPTION_PATH: Final = "/fe/recepcion/api/ecf"
SEED_PATH: Final = "/fe/autenticacion/api/semilla"
VALIDATION_PATH: Final = "/fe/autenticacion/api/validacioncertificado"
_OK: Final = 200
_TOKEN: Final = TypeAdapter(TokenAnswer)
_NOT_OPEN: Final = "a client not opened"

type DirectoryLookup = Callable[[Environment, str], Awaitable[DirectoryEntry | None]]


@final
@dataclass(frozen=True, slots=True)
class _Step:
    """A request of a delivery: its path under the buyer's base, and how a failure names it."""

    path: str
    name: str


_SEED: Final = _Step(SEED_PATH, "its seed")
_AUTHENTICATION: Final = _Step(VALIDATION_PATH, "its authentication")
_RECEPTION: Final = _Step(RECEPTION_PATH, "its reception")


def _now() -> datetime:
    return datetime.now(UTC)


@final
@dataclass(frozen=True, slots=True)
class Delivered:
    """The buyer answered a signed receipt: received, or not with DGII's reason code."""

    arecf: bytes
    received: bool
    reason: NotReceivedReason | None
    certificate: x509.Certificate


@final
@dataclass(frozen=True, slots=True)
class NotApplicable:
    """Delivery does not apply, and why."""

    reason: str


type Delivery = Delivered | NotApplicable


class BuyerUnavailableError(EcfUnavailableError):
    """The buyer's receiver failed, or answered nothing a receipt could be read from: retried later."""

    code: ClassVar[str] = "do_ecf_buyer_unavailable"

    def __init__(self, step: str) -> None:
        super().__init__(f"the buyer's receiver failed at {step}")


class ArecfMismatchError(EcfInvalidError):
    """A receipt about another document than the one delivered."""

    code: ClassVar[str] = "do_ecf_arecf_mismatch"

    def __init__(self) -> None:
        super().__init__("the buyer's receipt names another issuer, buyer or e-NCF")


@final
@dataclass(frozen=True, slots=True)
class SeedSigned:
    """A buyer's seed the issuer signed (see the module): who for, where it came from, when. No secret."""

    issuer_rnc: str
    buyer_rnc: str
    endpoint_host: str
    signed_at: datetime


AUDIT_LOGGER: Final = "ledger_country_do.audit.buyer_seeds"


def log_seed_signed(event: SeedSigned) -> None:
    """``event`` as a line of ``AUDIT_LOGGER`` at INFO: the default audit, the tax ids masked (§7.3).

    A tax id never reaches a log line whole: an audit that needs the full ids reads them from ``event`` in its
    own ``audit`` callable.
    """
    logging.getLogger(AUDIT_LOGGER).info(
        "signed a buyer's seed: issuer %s, buyer %s, endpoint %s, at %s",
        masked(event.issuer_rnc),
        masked(event.buyer_rnc),
        event.endpoint_host,
        event.signed_at.isoformat(),
    )


class BuyerOptions(TypedDict, total=False):
    """``BuyerClient``'s keywords: the trust policy and the outbound clients, then the rest.

    ``trust`` and ``http`` (the channel's ``OutboundHttp``) required; ``timeouts`` ``DEFAULT_TIMEOUTS``;
    ``resolve`` the system's resolver (``endpoints.system_resolve``); ``clock`` UTC now (the moment a
    receipt is received, a seed signed); ``audit`` ``log_seed_signed``.
    """

    trust: Required[CounterpartyTrust]
    http: Required[OutboundHttp]
    timeouts: DgiiTimeouts
    resolve: Resolve
    clock: Callable[[], datetime]
    audit: Callable[[SeedSigned], None]


@final
class BuyerClient:
    """Delivers to buyers' receivers (see the module); ``opened`` holds its connections."""

    __slots__ = (
        "_audit",
        "_clock",
        "_directory",
        "_http",
        "_outbound",
        "_resolve",
        "_signer",
        "_timeouts",
        "_trust",
    )

    def __init__(
        self, signer: XmlSigner, directory: DirectoryLookup, **options: Unpack[BuyerOptions]
    ) -> None:
        self._signer = signer
        self._directory = directory
        self._trust = options["trust"]
        self._outbound = options["http"]
        self._timeouts = options.get("timeouts", DEFAULT_TIMEOUTS)
        self._resolve = options.get("resolve", system_resolve)
        self._clock = options.get("clock", _now)
        self._audit = options.get("audit", log_seed_signed)
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[BuyerClient]:
        """This client with its connections open, for the length of the block."""
        timeouts = self._timeouts.outbound()
        async with self._outbound.client(BUYERS_CLIENT, timeouts=timeouts, keep_alive=False) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None

    def _opened(self) -> httpx.AsyncClient:
        if self._http is None:
            raise BuyerUnavailableError(_NOT_OPEN)
        return self._http

    async def _answer(
        self, at: Endpoint, step: _Step, *, headers: Mapping[str, str], document: tuple[str, bytes] | None
    ) -> tuple[int, bytes | None]:
        """The status and bounded body of the first checked address that takes the request.

        The next address is tried only when the previous could not be connected to: nothing was sent.
        """
        http = self._opened()
        files = None if document is None else {"xml": (document[0], document[1], "text/xml")}
        unreachable: httpx.TransportError | None = None
        for address in await pinned(at, self._resolve):
            request = http.build_request(
                "GET" if document is None else "POST",
                at.url(address, step.path),
                headers={"Host": at.authority, "Accept-Encoding": ACCEPTED_ENCODING, **headers},
                files=files,
                extensions={"sni_hostname": at.host},
            )
            try:
                streamed = await http.send(request, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                unreachable = error
                continue
            try:
                return streamed.status_code, await bounded(streamed)
            finally:
                await streamed.aclose()
        raise BuyerUnavailableError(step.name) from unreachable

    async def _exchange(
        self,
        at: Endpoint,
        step: _Step,
        deadline: float,
        *,
        headers: Mapping[str, str],
        document: tuple[str, bytes] | None = None,
    ) -> bytes:
        """One request to ``at``'s checked addresses: its answer's body, or ``BuyerUnavailableError``."""
        self._opened()
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout_at(min(loop.time() + self._timeouts.total, deadline)):
                status, body = await self._answer(at, step, headers=headers, document=document)
        except (httpx.TransportError, OSError) as error:
            raise BuyerUnavailableError(step.name) from error
        if status != _OK or body is None:
            raise BuyerUnavailableError(step.name)
        return body

    async def _token(self, at: Endpoint, deadline: float, draft: EcfDraft, buyer: str) -> str:
        seed = await self._exchange(at, _SEED, deadline, headers={"Accept": "application/xml"})
        try:
            signed = await self._signer.sign_seed(seed, audience=at.authority)
        except EcfInvalidError as error:
            raise BuyerUnavailableError(_SEED.name) from error
        self._audit(SeedSigned(draft.issuer.rnc, buyer, at.host, self._clock()))
        answer = await self._exchange(
            at,
            _AUTHENTICATION,
            deadline,
            headers={"Accept": "application/json"},
            document=("semilla.xml", signed),
        )
        try:
            return _TOKEN.validate_json(answer).token
        except ValidationError as error:
            raise BuyerUnavailableError(_AUTHENTICATION.name) from error

    async def deliver(self, environment: Environment, draft: EcfDraft, signed: bytes) -> Delivery:
        """``signed``, ``draft``'s accepted e-CF, delivered to its buyer (see the module)."""
        buyer = draft.buyer.rnc
        if buyer is None:
            return NotApplicable("the buyer has no RNC or cédula")
        entry = await self._directory(environment, buyer)
        if entry is None:
            return NotApplicable("the buyer is not an electronic receiver")
        self._opened()
        reception = endpoint(entry.reception)
        authentication = None if entry.authentication is None else endpoint(entry.authentication)
        deadline = asyncio.get_running_loop().time() + self._timeouts.call
        token = None if authentication is None else await self._token(authentication, deadline, draft, buyer)
        headers = {"Accept": "application/xml"} | (
            {} if token is None else {"Authorization": f"Bearer {token}"}
        )
        arecf = await self._exchange(
            reception,
            _RECEPTION,
            deadline,
            headers=headers,
            document=(f"{draft.issuer.rnc}{draft.encf}.xml", signed),
        )
        return self._receipt(arecf, draft, buyer, received_at=self._clock())

    def _receipt(self, arecf: bytes, draft: EcfDraft, buyer: str, *, received_at: datetime) -> Delivered:
        """``arecf`` once it is DGII's, signed, about ``draft``, by a certificate trusted for ``buyer``.

        Its signature first, then its reading (``read_inbound_arecf``: schema, values, hardened).
        """
        verified = verify_enveloped(arecf, INBOUND)
        receipt = read_inbound_arecf(arecf)
        if (receipt.issuer_rnc, receipt.buyer_rnc, receipt.encf) != (draft.issuer.rnc, buyer, draft.encf):
            raise ArecfMismatchError
        self._trust.check(verified, counterparty=buyer, signed_at=receipt.at, received_at=received_at)
        return Delivered(
            arecf=arecf, received=receipt.received, reason=receipt.reason, certificate=verified.certificate
        )
