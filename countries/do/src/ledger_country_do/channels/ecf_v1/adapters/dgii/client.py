"""DGII's web services for an emitter over HTTPS (httpx): the ``Authority`` port, and the directory.

Queries are ``GET``; what carries a document (the signed seed, an e-CF, an RFCE) is a multipart ``POST``.

* **Authentication.** DGII's seed, read by the hardened parser before anything signs it, is signed by the
  ``XmlSigner`` (``sign_seed``) and exchanged at ``validarsemilla`` for a token cached per environment until
  five minutes before it expires (never longer than an hour), fetched once at a time. A seed serves once, so
  a failed exchange starts again from a fresh seed: the two are retried together, never the validation alone.
  A 401 drops the token that was refused (not one another call has renewed since) and the call is made once
  more with a new one. Tokens live in memory only: never stored, logged, shown in a ``repr`` or put in an
  error, and forgotten when the client closes.
* **Retries.** What cannot change DGII's state (authentication, results, track ids, the directory) is retried
  twice, 0.2 s and 0.5 s apart, after no connection, a broken one or a 5xx. A document sent is never retried.
* **Submissions: one definition of "sent".** A submission ends in exactly one of three ways:

  - *DGII answered about it*: a 2xx that reads (``Received``, or the RFCE's ``Answer``), or a 400, DGII's
    refusal at the door (``Refused``; its message empty when the body does not read).
  - *DGII certainly did not take it* (``AuthorityUnavailableError``): no token could be had, no connection,
    a 429, a 401 even with a renewed token, or too little time left to send it at all.
  - *It may be at DGII* (``OutcomeUnknownError``): the connection broke or the time ran out after the
    request may have left, a 5xx, a 2xx that does not read (unreadable, too large, no track id), or any
    other status DGII does not document (a 404, a redirect). The caller asks DGII (track ids) before
    anything else, and never resends blindly.
* **Failures.** ``AuthorityUnavailableError``, its ``failure`` one of ``ports.Failure``.
* **Time.** Every attempt has a connect timeout and a total one; every call (token, retries and waits
  included) has an overall deadline, a minute by default (``DgiiTimeouts.call``), after which it fails
  ``timed_out`` (or, for a document already sent, its outcome is unknown). A document is sent only while the
  call has a whole attempt's time (``total``) left; with less it is not sent at all (``timed_out``).
* **Answers.** ``Accept-Encoding: gzip``: an answer is plain or a single gzip layer, any other encoding is
  refused; it is decompressed here as it streams and refused once it passes 1 MiB, never read whole first.
* **Transport.** Redirects are not followed; the environment's proxies and ``.netrc`` are ignored
  (``trust_env=False``); TLS is always verified; hosts are ``https`` origins (``endpoints``); the live
  environment only on DGII's hosts and never over an injected transport. Every file sent is named
  ``RNC + e-NCF + .xml`` (DT-DGII).
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict, Unpack, final, override
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
from pydantic import TypeAdapter, ValidationError

from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import (
    OFFICIAL_HOSTS,
    LiveHostRefusedError,
    Service,
    offered,
)
from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import (
    DirectoryAnswer,
    ReceptionAnswer,
    ResultAnswer,
    TokenAnswer,
    TrackIdAnswer,
)
from ledger_country_do.channels.ecf_v1.adapters.http import (
    ACCEPTED_ENCODING,
    DEFAULT_TIMEOUTS,
    MAX_ANSWER,
    bounded,
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
    TrackId,
)
from ledger_country_do.channels.ecf_v1.domain.errors import EcfInvalidError
from ledger_country_do.channels.ecf_v1.domain.notation import SANTO_DOMINGO, parse_dgii_moment
from ledger_country_do.channels.ecf_v1.domain.status_map import DgiiMessage
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.endpoints import DgiiHosts
lazy from ledger_country_do.channels.ecf_v1.adapters.dgii.wire import MessageAnswer
lazy from ledger_country_do.channels.ecf_v1.adapters.http import DgiiTimeouts
lazy from ledger_country_do.channels.ecf_v1.application.ports import Reception, XmlSigner
lazy from ledger_country_do.channels.ecf_v1.domain.encf import Encf
lazy from ledger_country_do.channels.ecf_v1.domain.environments import Environment

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


def _answer(answer: ResultAnswer) -> Answer:
    return Answer(
        answer.code, _messages(answer.messages), answer.sequence_used, rnc=answer.rnc, encf=answer.encf
    )


def _received_at(text: str) -> datetime | None:
    """``fechaRecepcion``: ISO 8601 or DGII's ``dd-MM-yyyy HH:mm:ss``; Santo Domingo's time if no zone."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return parse_dgii_moment(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=SANTO_DOMINGO)


def read_answer[T](model: TypeAdapter[T], response: httpx.Response, service: Service) -> T:
    """``response``'s JSON as ``model``; ``AuthorityUnavailableError`` (``unreadable``) otherwise."""
    if len(response.content) > MAX_ANSWER:
        raise AuthorityUnavailableError(Failure.UNREADABLE, service.value)
    try:
        return model.validate_json(response.content)
    except ValidationError as error:
        raise AuthorityUnavailableError(Failure.UNREADABLE, service.value) from error


def _submitted[T](model: TypeAdapter[T], response: httpx.Response, service: Service) -> T:
    """A submission's 2xx answer as ``model``; one that does not read leaves the outcome unknown."""
    try:
        return read_answer(model, response, service)
    except AuthorityUnavailableError as error:
        raise OutcomeUnknownError(service.value, Failure.UNREADABLE) from error


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

    @override
    def require_ready(self, environment: Environment) -> None:
        self._client(Service.RECEPTION)
        for service in Service:
            if offered(service, environment):
                self._url(service, environment)

    @override
    def attempt_limit(self) -> float:
        return self._timeouts.total

    def _client(self, service: Service) -> httpx.AsyncClient:
        if self._http is None:
            raise AuthorityUnavailableError(Failure.NOT_OPEN, service.value)
        return self._http

    def _url(self, service: Service, environment: Environment) -> str:
        if environment.live and self._transport is not None:
            raise LiveHostRefusedError
        return self._hosts.url(service, environment)

    def _deadline(self) -> float:
        return asyncio.get_running_loop().time() + self._timeouts.call

    async def _attempt(
        self, request: httpx.Request, service: Service, *, idempotent: bool, deadline: float
    ) -> httpx.Response | AuthorityUnavailableError:
        """One request: its answer, or a failure worth retrying; any other failure is raised."""
        http = self._client(service)
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout_at(min(loop.time() + self._timeouts.total, deadline)):
                streamed = await http.send(request, stream=True)
                try:
                    body = await bounded(streamed)
                finally:
                    await streamed.aclose()
        except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
            return AuthorityUnavailableError(Failure.UNREACHABLE, service.value)
        except (httpx.TransportError, TimeoutError) as error:
            reason = Failure.TIMED_OUT if loop.time() >= deadline else Failure.BROKEN
            if not idempotent:
                raise OutcomeUnknownError(service.value, reason) from error
            if reason is Failure.TIMED_OUT:
                raise AuthorityUnavailableError(reason, service.value) from error
            return AuthorityUnavailableError(reason, service.value)
        status = streamed.status_code
        if status == _TOO_MANY:
            return AuthorityUnavailableError(Failure.THROTTLED, service.value)
        if status >= _SERVER_ERROR and not idempotent:
            raise OutcomeUnknownError(service.value, Failure.SERVER_ERROR)
        if status >= _SERVER_ERROR:
            return AuthorityUnavailableError(Failure.SERVER_ERROR, service.value)
        if body is None and not idempotent:
            raise OutcomeUnknownError(service.value, Failure.UNREADABLE)
        if body is None:
            raise AuthorityUnavailableError(Failure.UNREADABLE, service.value)
        return httpx.Response(status, content=body, request=request)

    async def _retrying[R](
        self,
        attempt: Callable[[], Awaitable[R | AuthorityUnavailableError]],
        deadline: float,
        *,
        retried: bool,
    ) -> R:
        """``attempt``'s outcome, tried again after ``RETRY_DELAYS`` when ``retried`` and time is left."""
        delays = iter(RETRY_DELAYS if retried else ())
        while True:
            outcome = await attempt()
            if not isinstance(outcome, AuthorityUnavailableError):
                return outcome
            delay = next(delays, None)
            if delay is None or asyncio.get_running_loop().time() + delay >= deadline:
                raise outcome
            await self._sleep(delay)

    async def _call(
        self, request: httpx.Request, service: Service, *, idempotent: bool, deadline: float
    ) -> httpx.Response:
        return await self._retrying(
            lambda: self._attempt(request, service, idempotent=idempotent, deadline=deadline),
            deadline,
            retried=idempotent,
        )

    def _request(
        self,
        url: str,
        service: Service,
        token: str | None,
        *,
        params: Mapping[str, str] | None = None,
        document: tuple[str, bytes] | None = None,
    ) -> httpx.Request:
        headers = {"Accept": "application/json", "Accept-Encoding": ACCEPTED_ENCODING} | (
            {} if token is None else {"Authorization": f"Bearer {token}"}
        )
        files = None if document is None else {"xml": (document[0], document[1], "text/xml")}
        method = "GET" if document is None else "POST"
        return self._client(service).build_request(method, url, headers=headers, params=params, files=files)

    async def _signed(self, seed: bytes) -> bytes:
        try:
            texts(seed)
            return await self._signer.sign_seed(seed)
        except EcfInvalidError as error:
            raise AuthorityUnavailableError(Failure.UNREADABLE, Service.SEED.value) from error

    async def _authenticate(
        self, environment: Environment, deadline: float
    ) -> _Token | AuthorityUnavailableError:
        """A fresh seed, signed, exchanged for a token: one try of the two (a seed serves once)."""
        seed_request = self._request(self._url(Service.SEED, environment), Service.SEED, None)
        seed = await self._attempt(seed_request, Service.SEED, idempotent=True, deadline=deadline)
        if isinstance(seed, AuthorityUnavailableError):
            return seed
        if not seed.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, Service.SEED.value)
        signed = await self._signed(seed.content)
        validate_url = self._url(Service.VALIDATE_SEED, environment)
        request = self._request(validate_url, Service.VALIDATE_SEED, None, document=("semilla.xml", signed))
        response = await self._attempt(request, Service.VALIDATE_SEED, idempotent=True, deadline=deadline)
        if isinstance(response, AuthorityUnavailableError):
            return response
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
        return _Token(token.token, min(expires, self._clock() + TOKEN_CEILING) - TOKEN_MARGIN)

    async def _token(self, environment: Environment, service: Service, deadline: float) -> str:
        """The environment's token, fetched if need be; waiting for it, too, ends with the deadline."""
        lock = self._locks.setdefault(environment, asyncio.Lock())
        try:
            async with asyncio.timeout_at(deadline), lock:
                cached = self._tokens.get(environment)
                if cached is not None and cached.good_until > self._clock():
                    return cached.value
                fresh = await self._retrying(
                    lambda: self._authenticate(environment, deadline), deadline, retried=True
                )
                self._tokens[environment] = fresh
                return fresh.value
        except TimeoutError as error:
            raise AuthorityUnavailableError(Failure.TIMED_OUT, service.value) from error

    async def _authenticated(
        self,
        environment: Environment,
        service: Service,
        build: Callable[[str], httpx.Request],
        *,
        idempotent: bool,
    ) -> httpx.Response:
        deadline = self._deadline()
        token = await self._token(environment, service, deadline)
        self._require_time_to_send(service, deadline, idempotent=idempotent)
        response = await self._call(build(token), service, idempotent=idempotent, deadline=deadline)
        if response.status_code != _UNAUTHORIZED:
            return response
        cached = self._tokens.get(environment)
        if cached is not None and cached.value == token:
            del self._tokens[environment]
        renewed = await self._token(environment, service, deadline)
        self._require_time_to_send(service, deadline, idempotent=idempotent)
        return await self._call(build(renewed), service, idempotent=idempotent, deadline=deadline)

    def _require_time_to_send(self, service: Service, deadline: float, *, idempotent: bool) -> None:
        """A document is sent only with a whole attempt's time left: cut short, its fate is unknown."""
        if not idempotent and asyncio.get_running_loop().time() + self._timeouts.total > deadline:
            raise AuthorityUnavailableError(Failure.TIMED_OUT, service.value)

    def _send(
        self, environment: Environment, service: Service, name: str, signed: bytes
    ) -> Callable[[str], httpx.Request]:
        url = self._url(service, environment)
        return lambda token: self._request(url, service, token, document=(name, signed))

    async def _query(
        self, environment: Environment, service: Service, params: Mapping[str, str]
    ) -> httpx.Response:
        url = self._url(service, environment)
        response = await self._authenticated(
            environment,
            service,
            lambda token: self._request(url, service, token, params=params),
            idempotent=True,
        )
        if not response.is_success:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, service.value)
        return response

    async def _submit(
        self, environment: Environment, service: Service, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> httpx.Response | Refused:
        """A document sent once: DGII's 2xx answer, or its refusal at the door (400)."""
        build = self._send(environment, service, f"{issuer_rnc}{encf}.xml", signed)
        response = await self._authenticated(environment, service, build, idempotent=False)
        if response.status_code == _BAD_REQUEST:
            try:
                refusal = read_answer(_RECEPTION, response, service)
            except AuthorityUnavailableError:
                return Refused((DgiiMessage(str(_BAD_REQUEST), ""),))
            return Refused((DgiiMessage(refusal.error or str(_BAD_REQUEST), refusal.message or ""),))
        if response.status_code == _UNAUTHORIZED:
            raise AuthorityUnavailableError(Failure.UNDOCUMENTED, service.value)
        if not response.is_success:
            raise OutcomeUnknownError(service.value, Failure.UNDOCUMENTED)
        return response

    @override
    async def submit_ecf(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Reception:
        response = await self._submit(environment, Service.RECEPTION, issuer_rnc, encf, signed)
        if isinstance(response, Refused):
            return response
        answer = _submitted(_RECEPTION, response, Service.RECEPTION)
        if not answer.track_id:
            raise OutcomeUnknownError(Service.RECEPTION.value, Failure.UNDOCUMENTED)
        return Received(answer.track_id)

    @override
    async def submit_rfce(
        self, environment: Environment, issuer_rnc: str, encf: Encf, signed: bytes
    ) -> Answer | Refused:
        response = await self._submit(environment, Service.RFCE, issuer_rnc, encf, signed)
        if isinstance(response, Refused):
            return response
        return _answer(_submitted(_RESULT, response, Service.RFCE))

    @override
    async def result(self, environment: Environment, track_id: str) -> Answer:
        response = await self._query(environment, Service.RESULT, {"trackid": track_id})
        return _answer(read_answer(_RESULT, response, Service.RESULT))

    @override
    async def track_ids(
        self, environment: Environment, issuer_rnc: str, encf: Encf
    ) -> tuple[TrackId, ...] | None:
        if not offered(Service.TRACK_IDS, environment):
            return None
        params = {"rncemisor": issuer_rnc, "encf": str(encf)}
        response = await self._query(environment, Service.TRACK_IDS, params)
        entries = read_answer(_TRACK_IDS, response, Service.TRACK_IDS)
        received = [(_received_at(entry.received_at), entry.track_id) for entry in entries]
        ordered: list[TrackId] = []
        for moment, track_id in received:
            if moment is None:
                raise AuthorityUnavailableError(Failure.UNREADABLE, Service.TRACK_IDS.value)
            ordered.append(TrackId(track_id, moment))
        return tuple(sorted(ordered, key=lambda each: each.received_at))

    async def directory(self, environment: Environment, rnc: str) -> DirectoryEntry | None:
        """The receiver ``rnc`` in DGII's directory; ``None`` if it is no electronic receiver (F5, F6, F16).

        Only an entry naming ``rnc`` itself is taken: an answer listing another taxpayer's endpoints (whatever
        DGII's reason) never sends ``rnc``'s documents there.
        """
        response = await self._query(environment, Service.DIRECTORY, {"RNC": rnc})
        found = read_answer(_DIRECTORY, response, Service.DIRECTORY)
        entry = next((each for each in found if each.rnc.strip() == rnc), None)
        if entry is None:
            return None
        return DirectoryEntry(
            entry.name, entry.rnc, entry.reception, entry.approval, entry.authentication or None
        )
