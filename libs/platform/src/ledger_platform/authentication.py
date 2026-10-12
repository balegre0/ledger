"""Bearer tokens: OAuth 2.0 access tokens (JWT) checked against the identity provider's published keys.

The gateway checks tokens too; a service checks them again (defense in depth) and reads from them who calls.
A token is accepted only when all of this holds:

* it is a JWS in compact form of at most ``MAX_TOKEN_LENGTH`` ASCII characters;
* its header names an algorithm the policy allows, all of them asymmetric (``none`` and the HMAC family
  cannot be configured, so a token "signed" with the public key as an HMAC secret is refused), and a key id
  (``kid``) the provider publishes, under which it publishes a key for that algorithm;
* the signature verifies with that key: a key the token carries or points to (``jwk``, ``jku``, ``x5u``) is
  never used, nor fetched;
* ``iss`` is the policy's issuer and ``aud`` holds its audience;
* ``exp`` has not passed, and ``nbf`` and ``iat`` are not ahead, each give or take ``leeway`` seconds;
* ``sub`` and the organization claim are there and well formed.

Keys come from the provider's JWKS document and are kept for ``keys_ttl`` seconds. A token whose key id the
copy lacks refreshes it at once (the provider rotated its keys), but never sooner than ``refresh_floor``
seconds after the last attempt: a stream of tokens with invented key ids costs one fetch per floor, not one
per token. Requests that find the keys stale together share one fetch, which none of them can cancel by going
away. When a refresh fails, the keys already held keep working; with none at all a token cannot be checked
and the request gets a 503.

A service fetches with ``HttpKeys``: one HTTP client for the service's run, a resource the service opens at
start and closes at stop. ``fetch_keys``, a client per fetch, is for tools and tests that run no service.
Either way the environment cannot steer the fetch (no proxy, CA bundle or ``.netrc`` from it) and no redirect
is followed: the keys are where the configuration says.

A refused token is an ``InvalidTokenError`` that names the problem in a word (``expired``, ``audience``...).
Neither the token nor its claims are ever echoed or logged.
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, time
from typing import Annotated, ClassVar, Final, final, get_args
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping

import httpx
import structlog
from joserfc import jwt
from joserfc.errors import (
    BadSignatureError,
    ExpiredTokenError,
    InvalidClaimError,
    JoseError,
    KeyParameterError,
    MissingClaimError,
)
from joserfc.jwk import KeySet, KeySetSerialization
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, TypeAdapter, ValidationError
lazy from joserfc.jwk import Key

from ledger_kernel.access import InvalidSubjectError, Principal, parse_subject
from ledger_kernel.errors import LedgerError, UnauthenticatedError, UnavailableError
from ledger_kernel.ids import OrganizationId
from ledger_platform.options import InvalidOptionError, require_seconds
from ledger_platform.settings import TokenAlgorithm
lazy from ledger_platform.settings import ApiSettings

ASYMMETRIC_ALGORITHMS: Final = frozenset[str](get_args(TokenAlgorithm))
"""Every algorithm a token may be verified with: the ones the settings offer, all of them asymmetric."""
MAX_TOKEN_LENGTH: Final = 8192
"""Longer than any access token Zitadel issues; a longer one is refused unread."""
MAX_KEYS_SIZE: Final = 64 * 1024
"""The largest JWKS document read: a provider publishes a handful of keys."""
MAX_LEEWAY: Final = 300.0
ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
"""The caller's organization (asked for with the scope ``urn:zitadel:iam:user:resourceowner``)."""
PROJECT_ROLES_CLAIM: Final = "urn:zitadel:iam:org:project:roles"
"""Project roles, ``{role: {organization id: domain}}``; the per-project claim has the same shape."""
_RS256: Final = frozenset({"RS256"})
_DOTS: Final = 2
"""A compact JWS has three parts: header, payload, signature."""
_ORGANIZATIONS: Final = uuid5(NAMESPACE_URL, "urn:ledger:organization")
_logger = structlog.stdlib.get_logger(__name__)

type FetchKeys = Callable[[str], Awaitable[bytes]]
"""Fetches the JWKS document at a URL; ``PublishedKeys`` bounds the whole fetch. A test passes its own."""


class TokenProblem(StrEnum):
    """Why a token was refused, in a word a client can act on."""

    MALFORMED = "malformed"
    ALGORITHM = "algorithm"
    UNKNOWN_KEY = "unknown_key"
    SIGNATURE = "signature"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    ISSUER = "issuer"
    AUDIENCE = "audience"
    CLAIMS = "claims"


_PROBLEM_OF_CLAIM: Final[Mapping[str, TokenProblem]] = {
    "iss": TokenProblem.ISSUER,
    "aud": TokenProblem.AUDIENCE,
    "nbf": TokenProblem.NOT_YET_VALID,
    "iat": TokenProblem.NOT_YET_VALID,
}


class MissingTokenError(UnauthenticatedError):
    """The request carries no bearer token."""

    code: ClassVar[str] = "missing_token"

    def __init__(self) -> None:
        super().__init__("a bearer access token is required")


class InvalidTokenError(UnauthenticatedError):
    """The bearer token cannot be trusted; ``problem`` says why, and the token is never repeated."""

    code: ClassVar[str] = "invalid_token"

    problem: TokenProblem

    def __init__(self, problem: TokenProblem) -> None:
        super().__init__(f"the access token was refused: {problem}")
        self.problem = problem


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


class InvalidTokenOptionError(InvalidOptionError):
    """A token policy that cannot work."""

    code: ClassVar[str] = "invalid_token_option"
    label: ClassVar[str] = "token option"


class _KeysTooLargeError(ValueError):
    def __init__(self) -> None:
        super().__init__(f"the JWKS document is larger than {MAX_KEYS_SIZE} bytes")


def organization_of(provider_organization: str) -> OrganizationId:
    """The ledger id of the provider's organization: derived, so every service computes the same one."""
    return OrganizationId(uuid5(_ORGANIZATIONS, provider_organization))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class TokenPolicy:
    """What a token must be to be accepted (see the module). Durations are in seconds."""

    issuer: str
    audience: str
    keys_url: str
    algorithms: frozenset[str] = _RS256
    leeway: float = 60.0
    keys_ttl: float = 600.0
    refresh_floor: float = 10.0
    fetch_timeout: float = 5.0

    def __post_init__(self) -> None:
        for option, value in (
            ("issuer", self.issuer),
            ("audience", self.audience),
            ("keys_url", self.keys_url),
        ):
            if not value:
                raise InvalidTokenOptionError(option=option, requirement="not empty", value=value)
        if not self.algorithms or not self.algorithms <= ASYMMETRIC_ALGORITHMS:
            raise InvalidTokenOptionError(
                option="algorithms",
                requirement=f"some of {', '.join(sorted(ASYMMETRIC_ALGORITHMS))}",
                value=sorted(self.algorithms),
            )
        if not 0 <= self.leeway <= MAX_LEEWAY:  # NaN included
            raise InvalidTokenOptionError(
                option="leeway", requirement=f"between 0 and {MAX_LEEWAY} seconds", value=self.leeway
            )
        require_seconds(InvalidTokenOptionError, "keys_ttl", self.keys_ttl)
        require_seconds(InvalidTokenOptionError, "refresh_floor", self.refresh_floor)
        require_seconds(InvalidTokenOptionError, "fetch_timeout", self.fetch_timeout)

    @classmethod
    def of(cls, settings: ApiSettings) -> TokenPolicy:
        """The policy a service's settings describe."""
        return cls(
            issuer=settings.token_issuer,
            audience=settings.token_audience,
            keys_url=settings.token_jwks_url,
            algorithms=frozenset(settings.token_algorithms),
            leeway=float(settings.token_leeway_seconds),
        )


def _client() -> httpx.AsyncClient:
    """A client that follows no redirect and takes nothing from the environment: proxies, CA bundle, netrc."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False)


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """The JWKS document at ``url``: an error for any answer but 200 or a body over ``MAX_KEYS_SIZE`` bytes.

    The caller bounds the whole fetch, from connecting to the last byte (``PublishedKeys`` gives it
    ``fetch_timeout``): a provider that drips its answer cannot hold a request for longer.
    """
    body = bytearray()
    async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
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
    """The keys of a JWKS document by id. A document without a key that can be read is an error."""
    return {
        key.kid: key for key in KeySet.import_key_set(_JWKS.validate_json(document)) if key.kid is not None
    }


@final
class PublishedKeys:
    """The provider's signing keys by id, fetched and refreshed as the module describes."""

    __slots__ = ("_clock", "_fetch", "_fetched_at", "_keys", "_policy", "_refreshing", "_tried_at")

    def __init__(
        self, policy: TokenPolicy, *, fetch: FetchKeys = fetch_keys, clock: Callable[[], float] = monotonic
    ) -> None:
        self._policy = policy
        self._fetch = fetch
        self._clock = clock
        self._keys: Mapping[str, Key] = {}
        self._fetched_at: float | None = None
        self._tried_at: float | None = None
        self._refreshing: asyncio.Task[None] | None = None

    async def key(self, kid: str) -> Key | None:
        """The published key ``kid`` or ``None``; ``TokenKeysUnavailableError`` while no key was ever had."""
        if self._expired() or kid not in self._keys:
            await self._refreshed()
        if self._fetched_at is None:
            raise TokenKeysUnavailableError
        return self._keys.get(kid)

    def _expired(self) -> bool:
        fetched = self._fetched_at
        return fetched is None or self._clock() - fetched >= self._policy.keys_ttl

    async def _refreshed(self) -> None:
        """Wait for the fetch under way, or for one started now if the floor allows it; else return at once.

        The fetch runs in a task of its own that every request needing it waits for: requests that find the
        copy stale together share it, and one of them going away (a client that hung up) cancels its wait,
        never the fetch the others wait for.
        """
        refreshing = self._refreshing
        if refreshing is None:
            tried = self._tried_at
            now = self._clock()
            if tried is not None and now - tried < self._policy.refresh_floor:
                return
            self._tried_at = now
            refreshing = self._refreshing = asyncio.create_task(self._refresh(now))
        await asyncio.shield(refreshing)

    async def _refresh(self, started: float) -> None:
        try:
            async with asyncio.timeout(self._policy.fetch_timeout):
                document = await self._fetch(self._policy.keys_url)
            keys = _parse(document)
        except Exception:
            _logger.exception("tokens.keys_unavailable", url=self._policy.keys_url)
        else:
            self._keys = keys
            self._fetched_at = started
        finally:
            self._refreshing = None


class _Header(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    alg: str = Field(max_length=16)
    kid: str = Field(min_length=1, max_length=255)


_TEXT: Final = TypeAdapter(StrictStr)
"""The subject claim: text, whose form ``parse_subject`` then checks."""
_PROVIDER_ORGANIZATION: Final = TypeAdapter(
    Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
)
"""The organization claim: an id of the provider's, digits for Zitadel."""
_OBJECT: Final = TypeAdapter(dict[str, object])
_ROLES: Final = TypeAdapter(dict[str, dict[str, str]])


def _header_of(token: str) -> _Header:
    """The JOSE header of a compact JWS, read before anything is verified: which algorithm, which key."""
    if not token.isascii() or len(token) > MAX_TOKEN_LENGTH or token.count(".") != _DOTS:
        raise InvalidTokenError(TokenProblem.MALFORMED)
    encoded = token.partition(".")[0]
    try:
        return _Header.model_validate_json(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except ValueError as error:  # binascii.Error and ValidationError are both ValueErrors
        raise InvalidTokenError(TokenProblem.MALFORMED) from error


def _granted_roles(claims: Mapping[str, object], names: tuple[str, ...], organization: str) -> Iterator[str]:
    """The roles the role claims ``names`` grant in ``organization``."""
    for name in names:
        roles = _ROLES.validate_python(claims.get(name, {}))
        yield from (role for role, organizations in roles.items() if organization in organizations)


@final
class TokenVerifier:
    """Turns a bearer token into the ``Principal`` it proves, or refuses it (see the module)."""

    __slots__ = ("_claims", "_keys", "_policy", "_role_claims")

    def __init__(
        self, policy: TokenPolicy, *, keys: PublishedKeys | None = None, clock: Callable[[], float] = time
    ) -> None:
        self._policy = policy
        self._keys = PublishedKeys(policy) if keys is None else keys
        self._claims = jwt.JWTClaimsRegistry(
            now=lambda: int(clock()),
            leeway=int(policy.leeway),
            iss={"essential": True, "value": policy.issuer},
            aud={"essential": True, "value": policy.audience},
            exp={"essential": True},
            sub={"essential": True},
        )
        self._role_claims = (f"urn:zitadel:iam:org:project:{policy.audience}:roles", PROJECT_ROLES_CLAIM)

    async def verify(self, token: str) -> Principal:
        """The principal ``token`` proves, else ``InvalidTokenError`` (or ``TokenKeysUnavailableError``)."""
        header = _header_of(token)
        if header.alg not in self._policy.algorithms:
            raise InvalidTokenError(TokenProblem.ALGORITHM)
        key = await self._keys.key(header.kid)
        if key is None:
            raise InvalidTokenError(TokenProblem.UNKNOWN_KEY)
        return self._principal(self._verified_claims(token, key, header.alg))

    def _verified_claims(self, token: str, key: Key, algorithm: str) -> dict[str, object]:
        try:
            decoded = jwt.decode(token, key, algorithms=[algorithm])
        except BadSignatureError as error:
            raise InvalidTokenError(TokenProblem.SIGNATURE) from error
        except KeyParameterError as error:  # the key published under that id is not one for that algorithm
            raise InvalidTokenError(TokenProblem.ALGORITHM) from error
        except (JoseError, ValueError) as error:
            raise InvalidTokenError(TokenProblem.MALFORMED) from error
        try:
            claims = _OBJECT.validate_python(decoded.claims)
        except ValidationError as error:
            raise InvalidTokenError(TokenProblem.MALFORMED) from error
        try:
            self._claims.validate(claims)
        except ExpiredTokenError as error:
            raise InvalidTokenError(TokenProblem.EXPIRED) from error
        except (InvalidClaimError, MissingClaimError) as error:
            raise InvalidTokenError(_PROBLEM_OF_CLAIM.get(error.claim, TokenProblem.CLAIMS)) from error
        return claims

    def _principal(self, claims: Mapping[str, object]) -> Principal:
        try:
            subject = parse_subject(_TEXT.validate_python(claims.get("sub")))
            organization = _PROVIDER_ORGANIZATION.validate_python(claims.get(ORGANIZATION_CLAIM))
            roles = frozenset(_granted_roles(claims, self._role_claims, organization))
        except (ValidationError, InvalidSubjectError) as error:
            raise InvalidTokenError(TokenProblem.CLAIMS) from error
        return Principal(
            subject=subject,
            organization=organization_of(organization),
            provider_organization=organization,
            roles=roles,
        )
