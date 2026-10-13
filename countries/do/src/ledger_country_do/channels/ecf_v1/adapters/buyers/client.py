"""Delivering an accepted e-CF to an electronic buyer (spec §8.6.5 steps 2-4; DT-EMI; DT-DGII's directory).

The buyer is looked up in DGII's directory; no entry means it is no electronic receiver, and delivery does not
apply (the client hands it the printed representation). The directory's URLs are the buyer's to write, so
each is checked before anything is sent, and every request goes to an address checked public, never to a name
resolved again (``endpoints``). They are bases to which DGII's standard paths are appended (F5). When the
buyer publishes an authentication URL, its seed, read hardened, is signed and exchanged for its token
(``validacioncertificado``: issuer to buyer, DGII is not in the middle). The signed e-CF is posted to its
reception (multipart ``xml``, named RNC + e-NCF), and the answer must be a **signed ARECF**:

1. valid for DGII's schema, read by the hardened parser (no DOCTYPE, no entity, bounded);
2. its signature holding under the inbound profile (``verify_enveloped(profile=INBOUND)``);
3. naming this very document: issuer, buyer and e-NCF;
4. signed by a certificate the trust policy accepts (``CounterpartyTrust``): chained to the configured roots,
   valid when the ARECF says it acknowledged (``FechaHoraAcuseRecibo``, Santo Domingo's time) and at
   receipt here, those two moments close, its usage allowing it, and its subject naming the buyer's RNC or
   cédula. Revocation is not checked (a recorded gap).

``Estado`` 0 is received; 1 is not, with DGII's reason code. Requests follow no redirect, ignore the
environment's proxies, verify TLS for the buyer's name, accept a plain or single-gzip answer of at most
``MAX_ANSWER`` bytes read as it streams, and each has the per-attempt limit of ``DgiiTimeouts`` (``total``)
within the delivery's overall deadline (``call``). Nothing is retried here, and no connection is reused. The
receipt is the caller's to archive; the ``delivering`` state machine and its retries are v2-M4's.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, Required, TypedDict, Unpack, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError
lazy from cryptography import x509

from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import endpoint, pinned, system_resolve
from ledger_country_do.channels.ecf_v1.adapters.dgii.client import (
    ACCEPTED_ENCODING,
    DEFAULT_TIMEOUTS,
    bounded,
)
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import TokenAnswer
from ledger_country_do.channels.ecf_v1.adapters.signing.verifier import INBOUND, verify_enveloped
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.adapters.xml.schemas import checked
from ledger_country_do.channels.ecf_v1.adapters.xsd.published import Format
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError, EcfUnavailableError
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO
lazy from ledger_country_do.channels.ecf_v1.adapters.buyers.endpoints import Endpoint, Resolve
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.client import DgiiTimeouts, DirectoryEntry
lazy from ledger_country_do.channels.ecf_v1.adapters.signing.trust import CounterpartyTrust
lazy from ledger_country_do.channels.ecf_v1.application.ports import XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.drafts import EcfDraft
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

RECEPTION_PATH: Final = "/fe/recepcion/api/ecf"
SEED_PATH: Final = "/fe/autenticacion/api/semilla"
VALIDATION_PATH: Final = "/fe/autenticacion/api/validacioncertificado"
_OK: Final = 200
_RECEIVED: Final = "0"
_MOMENT: Final = "%d-%m-%Y %H:%M:%S"
_TOKEN: Final = TypeAdapter(TokenAnswer)
_DETAIL: Final = "DetalleAcusedeRecibo/"
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
    reason: int | None
    certificate: x509.Certificate


@final
@dataclass(frozen=True, slots=True)
class NotApplicable:
    """Delivery does not apply, and why."""

    reason: str


type Delivery = Delivered | NotApplicable


class BuyerUnavailableError(EcfUnavailableError):
    """The buyer's receiver failed or answered something other than what DGII's format says: retried later."""

    code: ClassVar[str] = "do_ecf_buyer_unavailable"

    def __init__(self, step: str) -> None:
        super().__init__(f"the buyer's receiver failed at {step}")


class ArecfMismatchError(EcfInvalidError):
    """A receipt about another document than the one delivered."""

    code: ClassVar[str] = "do_ecf_arecf_mismatch"

    def __init__(self) -> None:
        super().__init__("the buyer's receipt names another issuer, buyer or e-NCF")


class ArecfMomentError(EcfInvalidError):
    """A receipt whose ``FechaHoraAcuseRecibo`` fits DGII's pattern yet is no moment (a 31 February)."""

    code: ClassVar[str] = "do_ecf_arecf_moment_unreadable"

    def __init__(self) -> None:
        super().__init__("the buyer's receipt is dated with no real moment")


class BuyerOptions(TypedDict, total=False):
    """``BuyerClient``'s keywords: the trust policy, then timeouts, transport, resolver and clock.

    ``timeouts`` ``DEFAULT_TIMEOUTS``; ``transport`` ``None`` (httpx's own, TLS verified); ``resolve`` the
    system's resolver (``endpoints.system_resolve``); ``clock`` UTC now (the moment a receipt is received).
    """

    trust: Required[CounterpartyTrust]
    timeouts: DgiiTimeouts
    transport: httpx.AsyncBaseTransport | None
    resolve: Resolve
    clock: Callable[[], datetime]


def _integer(text: str | None) -> str | None:
    """An ``xs:integer``'s text as DGII's schema reads it: surrounding whitespace collapsed away."""
    return None if text is None else text.strip()


def _acknowledged(text: str | None) -> datetime:
    try:
        return datetime.strptime((text or "").strip(), _MOMENT).replace(tzinfo=SANTO_DOMINGO)
    except ValueError as error:
        raise ArecfMomentError from error


@final
class BuyerClient:
    """Delivers to buyers' receivers (see the module); ``opened`` holds its connections."""

    __slots__ = ("_clock", "_directory", "_http", "_resolve", "_signer", "_timeouts", "_transport", "_trust")

    def __init__(
        self, signer: XmlSigner, directory: DirectoryLookup, **options: Unpack[BuyerOptions]
    ) -> None:
        self._signer = signer
        self._directory = directory
        self._trust = options["trust"]
        self._timeouts = options.get("timeouts", DEFAULT_TIMEOUTS)
        self._transport = options.get("transport")
        self._resolve = options.get("resolve", system_resolve)
        self._clock = options.get("clock", _now)
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[BuyerClient]:
        """This client with its connections open, for the length of the block."""
        timeout = httpx.Timeout(self._timeouts.total, connect=self._timeouts.connect)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            verify=True,
            limits=httpx.Limits(max_keepalive_connections=0),
            transport=self._transport,
        ) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None

    def _opened(self) -> httpx.AsyncClient:
        if self._http is None:
            raise BuyerUnavailableError(_NOT_OPEN)
        return self._http

    async def _exchange(
        self,
        at: Endpoint,
        step: _Step,
        deadline: float,
        *,
        headers: Mapping[str, str],
        document: tuple[str, bytes] | None = None,
    ) -> bytes:
        """One request to ``at``'s checked address, its answer's body; ``BuyerUnavailableError`` otherwise."""
        http = self._opened()
        loop = asyncio.get_running_loop()
        files = None if document is None else {"xml": (document[0], document[1], "text/xml")}
        try:
            async with asyncio.timeout_at(min(loop.time() + self._timeouts.total, deadline)):
                address = await pinned(at, self._resolve)
                request = http.build_request(
                    "GET" if document is None else "POST",
                    at.url(address, step.path),
                    headers={"Host": at.authority, "Accept-Encoding": ACCEPTED_ENCODING, **headers},
                    files=files,
                    extensions={"sni_hostname": at.host},
                )
                streamed = await http.send(request, stream=True)
                try:
                    body = await bounded(streamed)
                finally:
                    await streamed.aclose()
        except (httpx.TransportError, OSError) as error:
            raise BuyerUnavailableError(step.name) from error
        if streamed.status_code != _OK or body is None:
            raise BuyerUnavailableError(step.name)
        return body

    async def _token(self, at: Endpoint, deadline: float) -> str:
        seed = await self._exchange(at, _SEED, deadline, headers={"Accept": "application/xml"})
        try:
            texts(seed)
            signed = await self._signer.sign_seed(seed)
        except EcfInvalidError as error:
            raise BuyerUnavailableError(_SEED.name) from error
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
        token = None if authentication is None else await self._token(authentication, deadline)
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
        """``arecf`` once it is DGII's, signed, about ``draft``, by a certificate trusted for ``buyer``."""
        checked(Format.ARECF, arecf, unsigned=False)
        verified = verify_enveloped(arecf, INBOUND)
        issuer, receiver, encf, state, reason, moment = texts(
            arecf,
            f"{_DETAIL}RNCEmisor",
            f"{_DETAIL}RNCComprador",
            f"{_DETAIL}eNCF",
            f"{_DETAIL}Estado",
            f"{_DETAIL}CodigoMotivoNoRecibido",
            f"{_DETAIL}FechaHoraAcuseRecibo",
        )
        if (_integer(issuer), _integer(receiver), encf) != (draft.issuer.rnc, buyer, str(draft.encf)):
            raise ArecfMismatchError
        self._trust.check(
            verified, counterparty=buyer, signed_at=_acknowledged(moment), received_at=received_at
        )
        return Delivered(
            arecf=arecf,
            received=_integer(state) == _RECEIVED,
            reason=None if reason is None else int(reason),
            certificate=verified.certificate,
        )
