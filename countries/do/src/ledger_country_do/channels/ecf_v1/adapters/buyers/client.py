"""Delivering an accepted e-CF to an electronic buyer, and an ACECF to its emitter (spec §8.6.5; DT-EMI).

The counterparty is looked up in DGII's directory (as the issuing company the call is for): no entry means it
is no electronic receiver, and delivery does not apply (``NotApplicable("no_directory_entry")``: the client
hands it the printed representation); an entry naming another taxpayer is never used
(``NotApplicable("entry_names_another_rnc")``). The directory's URLs are the counterparty's to write, so each
is checked before anything is sent, and every request goes to an address checked public, never to a name
resolved again (``endpoints``). They are bases to which DGII's standard paths are appended (F5).

**Authentication is the issuing company's switch** (``authenticate``, Decision 19; off by default upstream).
Off, no seed is ever signed: the document is posted without a token, and a counterparty that answers 401
fails with ``BuyerRequiresAuthenticationError`` (unavailable: the client may switch it on and retry). On, and
when the entry publishes an authentication URL, its seed is signed as the issuing company (``Signatures.seed``
refuses anything but DGII's ``SemillaModel``, read hardened; its audience the endpoint's host and port) and
exchanged for its token (``validacioncertificado``: issuer to counterparty, DGII is not in the middle). Every
seed signed is returned as a ``SeedSigned`` (the counterparty's tax id, the endpoint's host, the moment; never
the seed, the signature or a token) for the caller to audit with the tax id masked: in the outcome, or, when
the exchange fails after a seed was signed, in the error's ``seeds``.

**A signing oracle (recorded, not closed).** A buyer's seed has DGII's own format, and the issuer signs
whatever seed a buyer's authentication endpoint serves: a hostile endpoint can relay a seed it fetched from
DGII for this issuer's RNC, have it signed, and exchange it for a DGII token as this issuer (F14). Hence the
switch, off unless a buyer requires it, and the audit of every seed signed.

The signed e-CF is posted to the buyer's reception (multipart ``xml``, named RNC + e-NCF), and the answer must
be a **signed ARECF**:

1. its signature holding under the inbound profile (``verify_enveloped(profile=INBOUND)``);
2. read as every received document is (``xml.inbound.read_inbound_arecf``: hardened, bounded, valid for
   DGII's schema, its integers read as integers, its moment a real one);
3. naming this very document: issuer, buyer and e-NCF (``DeliveryDocument``);
4. signed by a certificate the trust policy accepts (``CounterpartyTrust``): chained to the configured roots,
   valid when the ARECF says it acknowledged (``FechaHoraAcuseRecibo``, Santo Domingo's time) and at
   receipt here, those two moments close, its usage allowing it, and its subject naming the buyer's RNC or
   cédula. Revocation is not checked (a recorded gap, Task 30).

``Estado`` 0 is received (and then carries no reason: one that does contradicts itself); 1 is not, with
DGII's reason code. A receipt that fails any of the four is **``ReceiptRefused``**, carrying its bytes and the
refusal's code: the caller archives it and marks the delivery failed, never retrying (the buyer would answer
the same). An ACECF is posted to the emitter's approval service (``send_acecf``): 200 is ``ApprovalSent``,
400 ``ApprovalRefused`` (the emitter refused it: not retried).

Requests go through the platform's ``OutboundHttp`` client ``BUYERS_CLIENT`` (traced and measured with the
host and status only, keeping no connection): they follow no redirect, ignore the environment's proxies,
verify TLS for the counterparty's name, accept a plain or single-gzip answer of at most ``MAX_ANSWER`` bytes
read as it streams, and each has the per-attempt limit of ``DgiiTimeouts`` (``total``) within the exchange's
overall deadline (``call``). A request goes to the next checked address only when the previous could not be
connected to; nothing is otherwise retried here, and no connection is reused.

**Failures, one classification.** A counterparty that could not be reached, failed, timed out, answered a
status not named above, or whose answer cannot be read as bytes (an encoding refused, over the size limit),
or whose seed or token does not read, is *unavailable* (``BuyerUnavailableError``: retried later). An
endpoint in DGII's directory that may not be reached is *invalid* (``BuyerEndpointRefusedError``). Both carry
the seeds signed before they were raised.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Final, Literal, Required, TypedDict, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError
lazy from cryptography import x509

from ledger_country_do.channels.ecf_v1.adapters.buyers.approvals import (
    ACECF_FILE,
    APPROVAL_PATH,
    ApprovalRefused,
    ApprovalSent,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import (
    BuyerEndpointRefusedError,
    endpoint,
    pinned,
    system_resolve,
)
from ledger_country_do.channels.ecf_v1.adapters.buyers.seeds import SeedSigned
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
from ledger_country_do.channels.ecf_v1.domain.exchanges import require_tax_id
from ledger_kernel.errors import InvalidInputError
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Endpoint, Resolve
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.directory import DirectoryLookup
lazy from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
lazy from ledger_country_do.channels.ecf_v1.application.ports import DirectoryEntry, Signatures, SigningAs
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment
lazy from ledger_country_do.channels.ecf_v1.domain.exchanges import NotReceivedReason
lazy from ledger_platform.http.outbound import OutboundHttp

RECEPTION_PATH: Final = "/fe/recepcion/api/ecf"
SEED_PATH: Final = "/fe/autenticacion/api/semilla"
VALIDATION_PATH: Final = "/fe/autenticacion/api/validacioncertificado"
_OK: Final = 200
_BAD_REQUEST: Final = 400
_UNAUTHORIZED: Final = 401
_TOKEN: Final = TypeAdapter(TokenAnswer)
_NOT_OPEN: Final = "a client not opened"


@final
@dataclass(frozen=True, slots=True)
class _Step:
    """A request of an exchange: its path under the counterparty's base, and how a failure names it."""

    path: str
    name: str
    accept: str


_SEED: Final = _Step(SEED_PATH, "its seed", "application/xml")
_AUTHENTICATION: Final = _Step(VALIDATION_PATH, "its authentication", "application/json")
_RECEPTION: Final = _Step(RECEPTION_PATH, "its reception", "application/xml")
_APPROVAL: Final = _Step(APPROVAL_PATH, "its approval service", "application/json")


def _now() -> datetime:
    return datetime.now(UTC)


def _read(status: int, body: bytes | None, step: _Step) -> bytes:
    """A 200's body that could be read; ``BuyerUnavailableError`` otherwise."""
    if status != _OK or body is None:
        raise BuyerUnavailableError(step.name)
    return body


def _refused(status: int, step: _Step) -> bool:
    """Whether an approval was refused (400) rather than taken (200); ``BuyerUnavailableError`` otherwise."""
    if status not in {_OK, _BAD_REQUEST}:
        raise BuyerUnavailableError(step.name)
    return status == _BAD_REQUEST


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryDocument:
    """What a receipt is checked against: the e-CF's issuer, its buyer and its e-NCF.

    Both parties are an RNC or a cédula: ``ExchangeInvalidError`` otherwise, naming the field, not the value.
    """

    issuer_rnc: str
    buyer_rnc: str
    encf: Encf

    def __post_init__(self) -> None:
        require_tax_id("delivery_document.issuer_rnc", self.issuer_rnc)
        require_tax_id("delivery_document.buyer_rnc", self.buyer_rnc)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Delivered:
    """The buyer answered a signed receipt: received, or not with DGII's reason code."""

    arecf: bytes = field(repr=False)
    received: bool
    reason: NotReceivedReason | None
    certificate: x509.Certificate = field(repr=False)
    """The certificate that signed the receipt (the buyer's)."""
    received_at: datetime
    """When the receipt arrived here."""
    seeds: tuple[SeedSigned, ...]


@final
@dataclass(frozen=True, slots=True)
class NotApplicable:
    """Delivery (or an approval) does not apply: DGII lists no receiver for the tax id, or another's entry."""

    reason: Literal["no_directory_entry", "entry_names_another_rnc"]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiptRefused:
    """The buyer answered a receipt that is refused: archived by the caller, the delivery failed, not retried.

    ``reason`` is the refusal's code (``do_ecf_signature_invalid``, ``do_ecf_arecf_mismatch``...).
    """

    arecf: bytes = field(repr=False)
    reason: str
    seeds: tuple[SeedSigned, ...]


type Delivery = Delivered | NotApplicable | ReceiptRefused
type Approval = ApprovalSent | ApprovalRefused | NotApplicable


class BuyerUnavailableError(EcfUnavailableError):
    """The counterparty's receiver failed, or answered nothing that could be read: retried later.

    ``seeds``: the seeds signed for the exchange before it failed, for the caller to audit.
    """

    code: ClassVar[str] = "do_ecf_buyer_unavailable"

    def __init__(self, step: str) -> None:
        self.seeds: tuple[SeedSigned, ...] = ()
        super().__init__(self.message(step))

    @staticmethod
    def message(step: str) -> str:
        """What the error says of ``step``."""
        return f"the buyer's receiver failed at {step}"


class BuyerRequiresAuthenticationError(BuyerUnavailableError):
    """The counterparty answered 401 to a document sent without a token: authentication is off.

    Off for the issuing company (Decision 19): switching it on, then retrying, may succeed.
    """

    code: ClassVar[str] = "do_ecf_buyer_requires_authentication"

    @staticmethod
    @override
    def message(step: str) -> str:
        return f"the buyer's receiver requires authentication at {step}, and it is switched off"


class ArecfMismatchError(EcfInvalidError):
    """A receipt about another document than the one delivered."""

    code: ClassVar[str] = "do_ecf_arecf_mismatch"

    def __init__(self) -> None:
        super().__init__("the buyer's receipt names another issuer, buyer or e-NCF")


class BuyerOptions(TypedDict, total=False):
    """``BuyerClient``'s keywords: the trust policy and the outbound clients, then the rest.

    ``trust`` and ``http`` (the channel's ``OutboundHttp``) required; ``timeouts`` ``DEFAULT_TIMEOUTS``;
    ``resolve`` the system's resolver (``endpoints.system_resolve``); ``clock`` UTC now (the moment a
    receipt is received, a seed signed, an approval sent).
    """

    trust: Required[CounterpartyTrust]
    http: Required[OutboundHttp]
    timeouts: DgiiTimeouts
    resolve: Resolve
    clock: Callable[[], datetime]


@final
@dataclass(slots=True)
class _Exchange:
    """One delivery or approval under way: whom for, until when, the seeds it has signed."""

    who: SigningAs
    counterparty: str
    deadline: float
    seeds: list[SeedSigned] = field(default_factory=list[SeedSigned])


@final
class BuyerClient:
    """Delivers to buyers' receivers and approvals to emitters (see the module).

    ``opened`` holds its connections.
    """

    __slots__ = ("_clock", "_directory", "_http", "_outbound", "_resolve", "_signer", "_timeouts", "_trust")

    def __init__(
        self, signer: Signatures, directory: DirectoryLookup, **options: Unpack[BuyerOptions]
    ) -> None:
        self._signer = signer
        self._directory = directory
        self._trust = options["trust"]
        self._outbound = options["http"]
        self._timeouts = options.get("timeouts", DEFAULT_TIMEOUTS)
        self._resolve = options.get("resolve", system_resolve)
        self._clock = options.get("clock", _now)
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

    async def _status(
        self,
        exchange: _Exchange,
        at: Endpoint,
        step: _Step,
        *,
        headers: Mapping[str, str],
        document: tuple[str, bytes] | None = None,
    ) -> tuple[int, bytes | None]:
        """One request to ``at``'s checked addresses, within an attempt's limit and the call's deadline."""
        self._opened()
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout_at(min(loop.time() + self._timeouts.total, exchange.deadline)):
                return await self._answer(at, step, headers=headers, document=document)
        except (httpx.TransportError, OSError) as error:
            raise BuyerUnavailableError(step.name) from error

    async def _body(
        self,
        exchange: _Exchange,
        at: Endpoint,
        step: _Step,
        *,
        headers: Mapping[str, str],
        document: tuple[str, bytes] | None = None,
    ) -> bytes:
        """``_status``'s body when it is 200 and reads; ``BuyerUnavailableError`` otherwise."""
        status, body = await self._status(exchange, at, step, headers=headers, document=document)
        return _read(status, body, step)

    async def _posted(
        self, exchange: _Exchange, at: Endpoint, step: _Step, token: str | None, document: tuple[str, bytes]
    ) -> tuple[int, bytes | None]:
        """``document`` posted to ``step`` with ``token`` if any: the status and bounded body.

        A 401 to a document sent without a token (authentication off) is ``BuyerRequiresAuthenticationError``.
        """
        headers = {"Accept": step.accept} | ({} if token is None else {"Authorization": f"Bearer {token}"})
        status, body = await self._status(exchange, at, step, headers=headers, document=document)
        if status == _UNAUTHORIZED and token is None:
            raise BuyerRequiresAuthenticationError(step.name)
        return status, body

    async def _token(self, exchange: _Exchange, at: Endpoint) -> str:
        """A token from ``at``: its seed signed as the issuing company (recorded), then exchanged."""
        seed = await self._body(exchange, at, _SEED, headers={"Accept": _SEED.accept})
        try:
            signed = await self._signer.seed(exchange.who, seed, audience=at.authority)
        except EcfInvalidError as error:
            raise BuyerUnavailableError(_SEED.name) from error
        exchange.seeds.append(
            SeedSigned(buyer=exchange.counterparty, endpoint_host=at.host, at=self._clock())
        )
        answer = await self._body(
            exchange,
            at,
            _AUTHENTICATION,
            headers={"Accept": _AUTHENTICATION.accept},
            document=("semilla.xml", signed),
        )
        try:
            return _TOKEN.validate_json(answer).token
        except ValidationError as error:
            raise BuyerUnavailableError(_AUTHENTICATION.name) from error

    async def _entry(
        self, who: SigningAs, environment: Environment, counterparty: str
    ) -> DirectoryEntry | NotApplicable:
        entry = await self._directory(who, environment, counterparty)
        if entry is None:
            return NotApplicable("no_directory_entry")
        if entry.rnc != counterparty:
            return NotApplicable("entry_names_another_rnc")
        return entry

    async def _authorized(
        self, exchange: _Exchange, entry: DirectoryEntry, *, authenticate: bool
    ) -> str | None:
        """A bearer token for the document's request when authentication is switched on and published."""
        at = None if not authenticate or entry.authentication is None else endpoint(entry.authentication)
        return None if at is None else await self._token(exchange, at)

    def _exchange(self, who: SigningAs, counterparty: str) -> _Exchange:
        self._opened()
        deadline = asyncio.get_running_loop().time() + self._timeouts.call
        return _Exchange(who=who, counterparty=counterparty, deadline=deadline)

    async def deliver(
        self,
        who: SigningAs,
        environment: Environment,
        document: DeliveryDocument,
        signed: bytes,
        *,
        authenticate: bool,
    ) -> Delivery:
        """``signed``, ``document``'s accepted e-CF, delivered as ``who`` to its buyer (see the module)."""
        found = await self._entry(who, environment, document.buyer_rnc)
        if isinstance(found, NotApplicable):
            return found
        reception = endpoint(found.reception)
        exchange = self._exchange(who, document.buyer_rnc)
        try:
            token = await self._authorized(exchange, found, authenticate=authenticate)
            named = (f"{document.issuer_rnc}{document.encf}.xml", signed)
            arecf = _read(*await self._posted(exchange, reception, _RECEPTION, token, named), _RECEPTION)
        except (BuyerUnavailableError, BuyerEndpointRefusedError) as error:
            error.seeds = tuple(exchange.seeds)
            raise
        seeds = tuple(exchange.seeds)
        try:
            return self._receipt(arecf, document, seeds, received_at=self._clock())
        except InvalidInputError as refused:
            return ReceiptRefused(arecf=arecf, reason=refused.code, seeds=seeds)

    def _receipt(
        self,
        arecf: bytes,
        document: DeliveryDocument,
        seeds: tuple[SeedSigned, ...],
        *,
        received_at: datetime,
    ) -> Delivered:
        """``arecf`` once it is DGII's, signed, about ``document``, by a certificate trusted for its buyer.

        Its signature first, then its reading (``read_inbound_arecf``: schema, values, hardened).
        """
        verified = verify_enveloped(arecf, INBOUND)
        receipt = read_inbound_arecf(arecf)
        named = (receipt.issuer_rnc, receipt.buyer_rnc, receipt.encf)
        if named != (document.issuer_rnc, document.buyer_rnc, document.encf):
            raise ArecfMismatchError
        self._trust.check(
            verified, counterparty=document.buyer_rnc, signed_at=receipt.at, received_at=received_at
        )
        return Delivered(
            arecf=arecf,
            received=receipt.received,
            reason=receipt.reason,
            certificate=verified.certificate,
            received_at=received_at,
            seeds=seeds,
        )

    async def send_acecf(
        self,
        who: SigningAs,
        environment: Environment,
        emitter_rnc: str,
        signed_acecf: bytes,
        *,
        authenticate: bool,
    ) -> Approval:
        """``signed_acecf``, ``who``'s commercial approval, to the approval service of ``emitter_rnc``.

        The emitter's ``urlAceptacion``, under the same rules as a delivery (see the module).
        """
        found = await self._entry(who, environment, emitter_rnc)
        if isinstance(found, NotApplicable):
            return found
        service = endpoint(found.approval)
        exchange = self._exchange(who, emitter_rnc)
        try:
            token = await self._authorized(exchange, found, authenticate=authenticate)
            status, _ = await self._posted(exchange, service, _APPROVAL, token, (ACECF_FILE, signed_acecf))
            refused = _refused(status, _APPROVAL)
        except (BuyerUnavailableError, BuyerEndpointRefusedError) as error:
            error.seeds = tuple(exchange.seeds)
            raise
        if refused:
            return ApprovalRefused(seeds=tuple(exchange.seeds))
        return ApprovalSent(sent_at=self._clock(), seeds=tuple(exchange.seeds))
