"""DGII's web services for an emitter over HTTPS (httpx): the ``Authority`` port, and the directory.

Queries are ``GET``; what carries a document (the signed seed, an e-CF, an RFCE) is a multipart ``POST``.

* **Authentication.** DGII's seed, read by the hardened parser before anything signs it, is signed by the
  ``XmlSigner`` (``sign_seed``) and exchanged at ``validarsemilla`` for a token cached per environment until
  five minutes before it expires (never longer than an hour), fetched once at a time; a 401 drops it and the
  call is made once more with a new one. Tokens live in memory only: never stored, logged, shown in a ``repr``
  or put in an error, and forgotten when the client closes.
* **Retries.** What cannot change DGII's state (seed, its validation, results, track ids, the directory) is
  retried twice, 0.2 s and 0.5 s apart. A document sent is never retried: when the connection breaks or the
  time runs out after its request may have left, ``OutcomeUnknownError`` tells the caller to ask DGII for the
  e-NCF's track ids first.
* **Failures.** No connection, a broken one, the time up, a 5xx or 429, an answer DGII does not document,
  unreadable, or larger than 1 MiB once decompressed (read as it streams, never whole first):
  ``AuthorityUnavailableError`` (``failure``: ``unreachable``, ``broken``, ``server_error``, ``undocumented``,
  ``unreadable``, ``refused`` (the signed seed), ``not_open``). DGII's refusal of a document (400 at
  reception) is an answer: ``Refused``.
* **Transport.** Every attempt has a connect timeout and a total one. Redirects are not followed; the
  environment's proxies and ``.netrc`` are ignored (``trust_env=False``); TLS is always verified; the live
  environment only on DGII's hosts (``endpoints``). Every file sent is named ``RNC + e-NCF + .xml`` (DT-DGII).
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import OFFICIAL_HOSTS, Service, offered
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import (
    DirectoryAnswer,
    ReceptionAnswer,
    ResultAnswer,
    TokenAnswer,
    TrackIdAnswer,
)
from ledger_country_do.channels.ecf_v1.adapters.xml.parsing import texts
from ledger_country_do.channels.ecf_v1.application.ports import (
    Answer,
    Authority,
    AuthorityUnavailableError,
    Failure,
    OutcomeUnknownError,
    Received,
    Refused,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import MessageAnswer
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception, XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

MAX_ANSWER: Final = 1024 * 1024
TOKEN_MARGIN: Final = timedelta(minutes=5)
TOKEN_CEILING: Final = timedelta(hours=1)
RETRY_DELAYS: Final = (0.2, 0.5)
_SERVER_ERROR: Final = 500
_TOO_MANY: Final = 429
_UNAUTHORIZED: Final = 401
_BAD_REQUEST: Final = 400
_TOKEN: Final = TypeAdapter(TokenAnswer)
_RECEPTION: Final = TypeAdapter(ReceptionAnswer)
_RESULT: Final = TypeAdapter(ResultAnswer)
_TRACK_IDS: Final = TypeAdapter(list[TrackIdAnswer])
_DIRECTORY: Final = TypeAdapter(list[DirectoryAnswer])

type Sleep = Callable[[float], Awaitable[None]]


@final
@dataclass(frozen=True, slots=True)
class DgiiTimeouts:
    """How long a call may take: to connect, and in all (each attempt)."""

    connect: float = 5.0
    total: float = 20.0


DEFAULT_TIMEOUTS: Final = DgiiTimeouts()


@final
@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """A receiver's service bases, from DGII's directory; ``authentication`` ``None`` if it publishes none."""

    name: str
    rnc: str
    reception: str
    approval: str
    authentication: str | None


class ClientOptions(TypedDict, total=False):
    """``DgiiClient``'s keywords, each optional.

    DGII's hosts (``OFFICIAL_HOSTS``), timeouts (``DEFAULT_TIMEOUTS``), a clock (UTC now), a sleep
    (``asyncio.sleep``) and an httpx transport (``None``: httpx's own, TLS verified).
    """

    hosts: DgiiHosts
    timeouts: DgiiTimeouts
    clock: Callable[[], datetime]
    sleep: Sleep
    transport: httpx.AsyncBaseTransport | None


@final
@dataclass(slots=True)
class _Token:
    value: str = field(repr=False)
    good_until: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _messages(found: list[MessageAnswer]) -> tuple[DgiiMessage, ...]:
    return tuple(DgiiMessage(str(message.code), message.text) for message in found)


def read_answer[T](model: TypeAdapter[T], response: httpx.Response, service: Service) -> T:
    """``response``'s JSON as ``model``; ``AuthorityUnavailableError`` (``unreadable``) otherwise."""
    if len(response.content) > MAX_ANSWER:
        raise AuthorityUnavailableError(Failure.UNREADABLE, service.value)
    try:
        return model.validate_json(response.content)
    except ValidationError as error:
        raise AuthorityUnavailableError(Failure.UNREADABLE, service.value) from error


async def _bounded(response: httpx.Response) -> bytes | None:
    """The body as it streams, decompressed; ``None`` once it passes ``MAX_ANSWER``."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_ANSWER:
            return None
    return bytes(body)


@final
class DgiiClient(Authority):
    """``Authority`` over HTTPS (see the module); ``opened`` holds its connections."""

    __slots__ = (
        "_clock",
        "_hosts",
        "_http",
        "_locks",
        "_signer",
        "_sleep",
        "_timeouts",
        "_tokens",
        "_transport",
    )

    def __init__(self, signer: XmlSigner, **options: Unpack[ClientOptions]) -> None:
        self._signer = signer
        self._hosts = options.get("hosts", OFFICIAL_HOSTS)
        self._timeouts = options.get("timeouts", DEFAULT_TIMEOUTS)
        self._clock = options.get("clock", _now)
        self._sleep = options.get("sleep", asyncio.sleep)
        self._transport = options.get("transport")
        self._tokens: dict[Environment, _Token] = {}
        self._locks: dict[Environment, asyncio.Lock] = {}
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[DgiiClient]:
        """This client with its connections open, for the length of the block; tokens forgotten after."""
        timeout = httpx.Timeout(self._timeouts.total, connect=self._timeouts.connect)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, verify=True, transport=self._transport
        ) as http:
            self._http = http
            try:
                yield self
            finally:
                self._http = None
                self._tokens.clear()

    def _client(self, service: Service) -> httpx.AsyncClient:
        if self._http is None:
            raise AuthorityUnavailableError(Failure.NOT_OPEN, service.value)
        return self._http

    async def _attempt(
        self, request: httpx.Request, service: Service, *, idempotent: bool
    ) -> httpx.Response | AuthorityUnavailableError:
        http = self._client(service)
        try:
            async with asyncio.timeout(self._timeouts.total):
                streamed = await http.send(request, stream=True)
                try:
                    body = await _bounded(streamed)
                finally:
                    await streamed.aclose()
        except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
            return AuthorityUnavailableError(Failure.UNREACHABLE, service.value)
        except (httpx.TransportError, TimeoutError) as error:
            if idempotent:
                return AuthorityUnavailableError(Failure.BROKEN, service.value)
            raise OutcomeUnknownError(service.value) from error
        if body is None:
            raise AuthorityUnavailableError(Failure.UNREADABLE, service.value)
        if streamed.status_code >= _SERVER_ERROR or streamed.status_code == _TOO_MANY:
            return AuthorityUnavailableError(Failure.SERVER_ERROR, service.value)
        return httpx.Response(streamed.status_code, content=body, request=request)

    async def _call(self, request: httpx.Request, service: Service, *, idempotent: bool) -> httpx.Response:
        delays = iter(RETRY_DELAYS if idempotent else ())
        while True:
            outcome = await self._attempt(request, service, idempotent=idempotent)
            if isinstance(outcome, httpx.Response):
                return outcome
            delay = next(delays, None)
            if delay is None:
                raise outcome
            await self._sleep(delay)

    def _request(
        self,
        url: str,
        service: Service,
        token: str | None,
        *,
        params: Mapping[str, str] | None = None,
        document: tuple[str, bytes] | None = None,
    ) -> httpx.Request:
        headers = {"Accept": "application/json"} | (
            {} if token is None else {"Authorization": f"Bearer {token}"}
        )
        files = None if document is None else {"xml": (document[0], document[1], "text/xml")}
        method = "GET" if document is None else "POST"
        return self._client(service).build_request(method, url, headers=headers, params=params, files=files)

    async def _signed_seed(self, environment: Environment) -> bytes:
        seed_url = self._hosts.url(Service.SEED, environment)
        seed = await self._call(self._request(seed_url, Service.SEED, None), Service.SEED, idempotent=True)
        if not seed.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.SEED.value)
        try:
            texts(seed.content)
            return await self._signer.sign_seed(seed.content)
        except EcfInvalidError as error:
            raise AuthorityUnavailableError(Failure.UNREADABLE, Service.SEED.value) from error

    async def _token(self, environment: Environment) -> str:
        async with self._locks.setdefault(environment, asyncio.Lock()):
            cached = self._tokens.get(environment)
            if cached is not None and cached.good_until > self._clock():
                return cached.value
            signed = await self._signed_seed(environment)
            validate_url = self._hosts.url(Service.VALIDATE_SEED, environment)
            request = self._request(
                validate_url, Service.VALIDATE_SEED, None, document=("semilla.xml", signed)
            )
            response = await self._call(request, Service.VALIDATE_SEED, idempotent=True)
            if response.status_code == _BAD_REQUEST:
                raise AuthorityUnavailableError(Failure.REFUSED, Service.VALIDATE_SEED.value)
            if not response.is_success:
                raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.VALIDATE_SEED.value)
            token = read_answer(_TOKEN, response, Service.VALIDATE_SEED)
            try:
                expires = datetime.fromisoformat(token.expires.strip())
            except ValueError as error:
                raise AuthorityUnavailableError(Failure.UNREADABLE, Service.VALIDATE_SEED.value) from error
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            good_until = min(expires, self._clock() + TOKEN_CEILING) - TOKEN_MARGIN
            self._tokens[environment] = _Token(token.token, good_until)
            return token.token

    async def _authenticated(
        self,
        environment: Environment,
        service: Service,
        build: Callable[[str], httpx.Request],
        *,
        idempotent: bool,
    ) -> httpx.Response:
        response = await self._call(build(await self._token(environment)), service, idempotent=idempotent)
        if response.status_code != _UNAUTHORIZED:
            return response
        self._tokens.pop(environment, None)
        return await self._call(build(await self._token(environment)), service, idempotent=idempotent)

    def _send(
        self, environment: Environment, service: Service, name: str, signed: bytes
    ) -> Callable[[str], httpx.Request]:
        url = self._hosts.url(service, environment)
        return lambda token: self._request(url, service, token, document=(name, signed))

    async def _query(
        self, environment: Environment, service: Service, params: Mapping[str, str]
    ) -> httpx.Response:
        url = self._hosts.url(service, environment)
        response = await self._authenticated(
            environment,
            service,
            lambda token: self._request(url, service, token, params=params),
            idempotent=True,
        )
        if not response.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, service.value)
        return response

    @override
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        build = self._send(environment, Service.RECEPTION, f"{issuer_rnc}{encf}.xml", signed)
        response = await self._authenticated(environment, Service.RECEPTION, build, idempotent=False)
        if not response.is_success and response.status_code != _BAD_REQUEST:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.RECEPTION.value)
        answer = read_answer(_RECEPTION, response, Service.RECEPTION)
        if response.is_success and answer.track_id:
            return Received(answer.track_id)
        if response.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.RECEPTION.value)
        return Refused((DgiiMessage(answer.error or str(response.status_code), answer.message or ""),))

    @override
    async def submit_rfce(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        build = self._send(environment, Service.RFCE, f"{issuer_rnc}{encf}.xml", signed)
        response = await self._authenticated(environment, Service.RFCE, build, idempotent=False)
        if response.status_code == _BAD_REQUEST:
            refusal = read_answer(_RECEPTION, response, Service.RFCE)
            return Refused((DgiiMessage(refusal.error or "400", refusal.message or ""),))
        if not response.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.RFCE.value)
        answer = read_answer(_RESULT, response, Service.RFCE)
        return Answer(answer.code, _messages(answer.messages), answer.sequence_used)

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        response = await self._query(environment, Service.RESULT, {"trackid": track_id})
        answer = read_answer(_RESULT, response, Service.RESULT)
        return Answer(answer.code, _messages(answer.messages), answer.sequence_used)

    @override
    async def track_ids(
        self, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[str, ...] | None:
        if not offered(Service.TRACK_IDS, environment):
            return None
        response = await self._query(
            environment, Service.TRACK_IDS, {"rncemisor": issuer_rnc, "encf": str(encf)}
        )
        entries = read_answer(_TRACK_IDS, response, Service.TRACK_IDS)
        return tuple(entry.track_id for entry in sorted(entries, key=lambda entry: entry.received_at))

    async def directory(self, environment: Environment, rnc: str) -> DirectoryEntry | None:
        """The receiver ``rnc`` in DGII's directory; ``None`` if it is no electronic receiver (F5, F6)."""
        response = await self._query(environment, Service.DIRECTORY, {"RNC": rnc})
        found = read_answer(_DIRECTORY, response, Service.DIRECTORY)
        if not found:
            return None
        entry = found[0]
        return DirectoryEntry(
            entry.name, entry.rnc, entry.reception, entry.approval, entry.authentication or None
        )
