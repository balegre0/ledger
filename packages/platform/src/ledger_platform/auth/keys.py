"""The identity provider's signing keys: fetched from its JWKS document, held and refreshed.

Keys come from the provider's JWKS document and are fresh for ``keys_ttl`` seconds. Past that, a token whose
key is held is still answered at once while a refresh runs behind it (stale while revalidate): a provider that
stalls slows no request whose key is known. A request waits for the refresh only when it cannot be answered
without one: no key was ever obtained, the token's key id is not held (the provider rotated its keys), or the
keys held are older than ``keys_max_age`` and no refresh has failed since the last one that succeeded. That
ceiling is soft: availability wins while the provider is down (a key can only be retired at the provider,
which is what is down), so once a refresh past the ceiling has failed, the keys held are served without
waiting, a refresh still set off behind them once per floor, until one succeeds. Crossing the ceiling is
logged once (``tokens.keys_past_ceiling``, a warning) and so is the copy being fresh again
(``tokens.keys_refreshed``); ``ledger_auth_keys_age_seconds`` exports the age of the keys held, for alerts.
A refresh never starts sooner than ``refresh_floor`` seconds after the last one: a stream of tokens with
invented key ids costs one fetch per floor, not one per token. Requests share the one refresh under way,
which none of them can cancel by going away. When a refresh fails (including a document without a single
usable key), the keys already held keep working; with none at all a token cannot be checked and the request
gets a 503.

A service fetches with ``HttpKeys``: one HTTP client for the service's run, a resource the service opens at
start and closes at stop. ``fetch_keys``, a client per fetch, is for tools and tests that run no service.
Either way the environment cannot steer the fetch (no proxy, CA bundle or ``.netrc`` from it), no redirect
is followed (the keys are where the configuration says), and the document is asked for uncompressed and
refused compressed, so ``MAX_KEYS_SIZE`` bounds the bytes received, not a bomb they would inflate to.
"""

import asyncio
import math
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from time import monotonic
from typing import ClassVar, Final, Protocol, final
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import httpx
import structlog
from joserfc.jwk import KeySet, KeySetSerialization
from prometheus_client import Gauge
from pydantic import TypeAdapter
lazy from joserfc.jwk import Key
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.errors import LedgerError, UnavailableError, excerpt

MAX_KEYS_SIZE: Final = 64 * 1024
"""The largest JWKS document read: a provider publishes a handful of keys."""
_logger = structlog.stdlib.get_logger(__name__)

type FetchKeys = Callable[[str], Awaitable[bytes]]
"""Fetches the JWKS document at a URL; ``PublishedKeys`` bounds the whole fetch. A test passes its own."""


class KeysPolicy(Protocol, metaclass=ABCMeta):
    """Where the keys are published and how long they are kept, in seconds: what ``TokenPolicy`` says."""

    @property
    @abstractmethod
    def keys_url(self) -> str:
        """The URL of the provider's JWKS document."""

    @property
    @abstractmethod
    def keys_ttl(self) -> float:
        """Seconds the keys are fresh for."""

    @property
    @abstractmethod
    def keys_max_age(self) -> float:
        """Seconds past which a request waits for a refresh (the soft ceiling)."""

    @property
    @abstractmethod
    def refresh_floor(self) -> float:
        """Seconds at least between two refreshes."""

    @property
    @abstractmethod
    def fetch_timeout(self) -> float:
        """Seconds a fetch may take, all of it."""


class TokenKeysUnavailableError(UnavailableError):
    """The provider's keys could not be fetched and none are held: no token can be checked now."""

    code: ClassVar[str] = "token_keys_unavailable"

    def __init__(self) -> None:
        super().__init__("the identity provider's signing keys could not be fetched")


class KeysClientClosedError(LedgerError):
    """``HttpKeys`` was asked to fetch while its client was not open: a service that never opened it."""

    code: ClassVar[str] = "keys_client_closed"

    def __init__(self) -> None:
        super().__init__("the client that fetches the provider's keys is not open")


class _KeysTooLargeError(ValueError):
    def __init__(self) -> None:
        super().__init__(f"the JWKS document is larger than {MAX_KEYS_SIZE} bytes")


class _KeysEncodedError(ValueError):
    def __init__(self, encoding: str) -> None:
        super().__init__(f"the JWKS document came encoded ({excerpt(encoding)}) though asked for as it is")


class _NoKeysError(ValueError):
    def __init__(self) -> None:
        super().__init__("the JWKS document holds no key with an id")


def _client() -> httpx.AsyncClient:
    """A client that follows no redirect and takes nothing from the environment: proxies, CA bundle, netrc."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False)


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """The JWKS document at ``url``: an error for any answer but 200 or a body over ``MAX_KEYS_SIZE`` bytes.

    The caller bounds the whole fetch, from connecting to the last byte (``PublishedKeys`` gives it
    ``fetch_timeout``): a provider that drips its answer cannot hold a request for longer.
    """
    body = bytearray()
    asked = {"Accept": "application/json", "Accept-Encoding": "identity"}
    async with client.stream("GET", url, headers=asked) as response:
        response.raise_for_status()
        encodings = response.headers.get_list("Content-Encoding", split_commas=True)
        if any(encoding.strip().lower() != "identity" for encoding in encodings):
            raise _KeysEncodedError(", ".join(encodings))
        async for chunk in response.aiter_raw():
            body += chunk
            if len(body) > MAX_KEYS_SIZE:
                raise _KeysTooLargeError
    return bytes(body)


async def fetch_keys(url: str) -> bytes:
    """The JWKS document at ``url``, with a client of its own: for tools and tests that run no service."""
    async with _client() as client:
        return await _fetch(client, url)


@final
class HttpKeys:
    """Fetches JWKS documents with one HTTP client, open for a service's run: a resource of the service.

    The service enters ``opened()`` when it starts and leaves it when it stops (``Service``'s resources hook),
    which closes the client and its connections in order. A fetch while it is not open raises
    ``KeysClientClosedError``, which ``PublishedKeys`` treats as any failed fetch.
    """

    __slots__ = ("_client",)

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def opened(self) -> AsyncGenerator[HttpKeys]:
        """This fetcher, its client open for the length of the block."""
        async with _client() as client:
            self._client = client
            try:
                yield self
            finally:
                self._client = None

    async def __call__(self, url: str) -> bytes:
        """The JWKS document at ``url``, as ``fetch_keys`` reads it."""
        client = self._client
        if client is None:
            raise KeysClientClosedError
        return await _fetch(client, url)


_JWKS: Final = TypeAdapter(KeySetSerialization)


def _parse(document: bytes) -> dict[str, Key]:
    """The keys of a JWKS document by the id it publishes them under.

    A key without an id is left out (joserfc would name it by its thumbprint, which no token carries), as is
    one of a type joserfc does not know; a document without a single key left is an error.
    """
    published = _JWKS.validate_json(document)
    named = {kid for key in published["keys"] if isinstance(kid := key.get("kid"), str)}
    keys: dict[str, Key] = {}
    for key in KeySet.import_key_set(published):
        match key.kid:
            case str() as kid if kid in named:
                keys[kid] = key
            case _:
                pass
    if not keys:
        raise _NoKeysError
    return keys


@final
class PublishedKeys:
    """The provider's signing keys by id, fetched and refreshed as the module describes."""

    __slots__ = (
        "_clock",
        "_failed",
        "_fetch",
        "_fetched_at",
        "_keys",
        "_past_ceiling",
        "_policy",
        "_refreshing",
        "_tried_at",
    )

    def __init__(
        self,
        policy: KeysPolicy,
        *,
        fetch: FetchKeys = fetch_keys,
        clock: Callable[[], float] = monotonic,
        registry: CollectorRegistry | None = None,
    ) -> None:
        """``registry``: where the age of the keys is exported (a service's); ``None``, nowhere."""
        self._policy = policy
        self._fetch = fetch
        self._clock = clock
        self._keys: Mapping[str, Key] = {}
        self._fetched_at: float | None = None
        self._tried_at: float | None = None
        self._refreshing: asyncio.Task[None] | None = None
        self._failed = False
        """Whether a refresh failed since the last one that succeeded."""
        self._past_ceiling = False
        """Whether the keys held were seen past ``keys_max_age`` (and the crossing logged)."""
        if registry is not None:
            Gauge(
                "ledger_auth_keys_age_seconds",
                "Seconds since the identity provider's signing keys held were fetched; +Inf while none were.",
                registry=registry,
            ).set_function(self._age)

    async def key(self, kid: str) -> Key | None:
        """The published key ``kid`` or ``None``; ``TokenKeysUnavailableError`` while no key was ever had.

        A held key is answered at once while the keys are fresh; past ``keys_ttl`` it sets off a refresh it
        does not wait for; past ``keys_max_age`` it waits for that refresh, unless one failed since the last
        success (the soft ceiling). A key not held waits for a refresh, if the floor allows one.
        """
        age = self._age()
        held = self._keys.get(kid)
        if age < self._policy.keys_ttl and held is not None:
            return held
        beyond_ceiling = age >= self._policy.keys_max_age and self._fetched_at is not None
        if beyond_ceiling and not self._past_ceiling:
            self._past_ceiling = True
            _logger.warning("tokens.keys_past_ceiling", age=age, ceiling=self._policy.keys_max_age)
        refreshing = self._refreshing_now()
        waits = held is None or (beyond_ceiling and not self._failed)
        if refreshing is not None and waits:
            await asyncio.shield(refreshing)
        if self._fetched_at is None:
            raise TokenKeysUnavailableError
        return self._keys.get(kid)

    def _age(self) -> float:
        """Seconds since the keys held were fetched; infinite while none were."""
        fetched = self._fetched_at
        return math.inf if fetched is None else self._clock() - fetched

    def _refreshing_now(self) -> asyncio.Task[None] | None:
        """The refresh under way, or one started now if the floor allows it; else ``None``.

        The refresh runs in a task of its own: requests that need it wait for it together, and one of them
        going away (a client that hung up) cancels its wait, never the refresh the others wait for.
        """
        refreshing = self._refreshing
        if refreshing is None:
            tried = self._tried_at
            now = self._clock()
            if tried is not None and now - tried < self._policy.refresh_floor:
                return None
            self._tried_at = now
            refreshing = self._refreshing = asyncio.create_task(self._refresh(now))
        return refreshing

    async def _refresh(self, started: float) -> None:
        try:
            async with asyncio.timeout(self._policy.fetch_timeout):
                document = await self._fetch(self._policy.keys_url)
            keys = _parse(document)
        except Exception:
            self._failed = True
            _logger.exception("tokens.keys_unavailable", url=self._policy.keys_url)
        else:
            self._keys = keys
            self._fetched_at = started
            self._failed = False
            if self._past_ceiling:
                self._past_ceiling = False
                _logger.info("tokens.keys_refreshed", url=self._policy.keys_url)
        finally:
            self._refreshing = None
