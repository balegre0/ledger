"""Bearer tokens: OAuth 2.0 access tokens (JWT) checked against the identity provider's published keys.

The gateway checks tokens too; a service checks them again (defense in depth) and reads from them who calls.
A token is accepted only when all of this holds:

* it is a JWS in compact form of at most ``MAX_TOKEN_LENGTH`` ASCII characters, whose header is well
  formed (``crit``, when there, a list of the names of other headers);
* its header types it as an access token, when it types it at all (``typ``: ``JWT``, or RFC 9068's
  ``at+jwt``);
* its header names an algorithm the policy allows, all of them asymmetric (``none`` and the HMAC family
  cannot be configured, so a token "signed" with the public key as an HMAC secret is refused), and a key id
  (``kid``) the provider publishes, under which it publishes a key for that algorithm;
* the signature verifies with that key: a key the token carries or points to (``jwk``, ``jku``, ``x5u``) is
  never used, nor fetched;
* ``iss`` is the policy's issuer and ``aud`` (a text, or a list of texts) holds its audience;
* ``exp``, ``nbf`` and ``iat`` are finite numbers; ``exp`` is ahead of now by more than ``leeway`` seconds
  ago (``exp`` itself is exclusive, RFC 7519 section 4.1.4), and ``nbf`` and ``iat`` are at most ``leeway``
  seconds ahead;
* it is an access token, not an ID token of the same provider for the same project (whose ``aud`` holds the
  project's id too, so the audience cannot tell them apart): it carries ``jti`` and ``client_id``, both
  required of a JWT access token by RFC 9068, and none of the claims an OpenID Connect ID token carries,
  ``azp``, ``nonce``, ``at_hash`` and ``c_hash``. Zitadel v4 types both kinds ``JWT``; its access tokens
  carry a ``jti`` and no ``azp``, its ID tokens an ``azp`` (their client's id) and no ``jti``. The rule is
  Zitadel's: a second provider would get an access-token rule of its own in ``TokenPolicy`` (such as
  requiring ``typ`` ``at+jwt`` where the provider issues it), since some providers put ``azp`` in access
  tokens too;
* ``sub`` and the organization claim are there and well formed.

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

A refused token is an ``InvalidTokenError`` that names the problem in a word (``expired``, ``audience``...)
and is raised with no cause or context attached: neither the token nor its claims are ever echoed or
logged.
"""

import asyncio
import base64
import contextlib
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, time
from typing import Annotated, ClassVar, Final, Never, final, get_args
from uuid import NAMESPACE_URL, uuid5
lazy from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping

import httpx
import structlog
from joserfc import jwt
from joserfc.errors import BadSignatureError, InvalidKeyCurveError, JoseError, KeyParameterError
from joserfc.jwk import KeySet, KeySetSerialization
from joserfc.jws import JWSRegistry
from prometheus_client import Gauge
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, TypeAdapter, ValidationError
lazy from joserfc.jwk import Key
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.access import InvalidSubjectError, Principal, parse_subject
from ledger_kernel.errors import LedgerError, UnauthenticatedError, UnavailableError, excerpt
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
MAX_LEEWAY: Final = 300
MAX_HEADER_SIZE: Final = 2048
"""The largest JOSE header verified, in bytes: room for a header that carries a key, so that such a token is
refused for its signature, not for its size (joserfc's own limit is 512)."""
ORGANIZATION_CLAIM: Final = "urn:zitadel:iam:user:resourceowner:id"
"""The caller's organization (asked for with the scope ``urn:zitadel:iam:user:resourceowner``)."""
ACCESS_TOKEN_TYPES: Final = frozenset({"jwt", "at+jwt", "application/jwt", "application/at+jwt"})
"""The ``typ`` headers of an access token, in lower case (RFC 7515 4.1.9 allows the ``application/`` form)."""
ACCESS_TOKEN_CLAIMS: Final = ("jti", "client_id")
"""Claims every access token carries (RFC 9068 2.2): text, not empty."""
ID_TOKEN_CLAIMS: Final = frozenset({"azp", "nonce", "at_hash", "c_hash"})
"""Claims of an OpenID Connect ID token that no access token carries: a token with any of them is refused."""
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
    WRONG_KIND = "not_access_token"
    CLAIMS = "claims"


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


class _KeysEncodedError(ValueError):
    def __init__(self, encoding: str) -> None:
        super().__init__(f"the JWKS document came encoded ({excerpt(encoding)}) though asked for as it is")


class _NoKeysError(ValueError):
    def __init__(self) -> None:
        super().__init__("the JWKS document holds no key with an id")


def _refuse(problem: TokenProblem) -> Never:
    """Refuse the token for ``problem``. Never called while an exception is handled: it would be attached."""
    raise InvalidTokenError(problem)


def _valid[T](adapter: TypeAdapter[T], value: object, problem: TokenProblem) -> T:
    """``value`` as ``adapter`` reads it, else the token is refused for ``problem``."""
    with contextlib.suppress(ValidationError):
        return adapter.validate_python(value)
    _refuse(problem)


def _problem_of(error: Exception) -> TokenProblem:
    """What a failure of joserfc to verify a token says of it."""
    match error:
        case BadSignatureError():
            return TokenProblem.SIGNATURE
        case KeyParameterError() | InvalidKeyCurveError():  # the published key is not one for that algorithm
            return TokenProblem.ALGORITHM
        case _:  # TypeError among them: a header joserfc reads before it checks its type
            return TokenProblem.MALFORMED


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
    leeway: int = 60
    keys_ttl: float = 600.0
    keys_max_age: float = 1200.0
    """The soft ceiling: keys older than this are refreshed before a request is answered with them, unless a
    refresh failed since the last success (at least ``keys_ttl``; by default twice its default)."""
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
        if not 0 <= self.leeway <= MAX_LEEWAY:
            raise InvalidTokenOptionError(
                option="leeway", requirement=f"between 0 and {MAX_LEEWAY} seconds", value=self.leeway
            )
        require_seconds(InvalidTokenOptionError, "keys_ttl", self.keys_ttl)
        require_seconds(InvalidTokenOptionError, "keys_max_age", self.keys_max_age, floor=self.keys_ttl)
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
            leeway=settings.token_leeway_seconds,
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
        policy: TokenPolicy,
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


class _Header(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    alg: str = Field(max_length=16)
    kid: str = Field(min_length=1, max_length=255)
    typ: str | None = Field(default=None, max_length=32)
    crit: tuple[str, ...] = Field(default=(), min_length=1)
    """Headers the token says must be understood: when there, a non-empty list of names (RFC 7515 4.1.11)."""


_TEXT: Final = TypeAdapter(StrictStr)
"""The subject claim: text, whose form ``parse_subject`` then checks."""
_IDENTIFIER: Final = TypeAdapter(Annotated[str, StringConstraints(strict=True, pattern=r"\S")])
"""``jti`` and ``client_id``: text with something in it besides white space."""
_PROVIDER_ORGANIZATION: Final = TypeAdapter(
    Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
)
"""The organization claim: an id of the provider's, digits for Zitadel."""
_AUDIENCES: Final = TypeAdapter(StrictStr | list[StrictStr])
"""``aud``: one audience, or a list of them."""
_OBJECT: Final = TypeAdapter(dict[str, object])
_ROLES: Final = TypeAdapter(dict[str, dict[str, str]])


def _header_of(token: str) -> _Header:
    """The JOSE header of a compact JWS, read before anything is verified: which algorithm, which key."""
    if not token.isascii() or len(token) > MAX_TOKEN_LENGTH or token.count(".") != _DOTS:
        _refuse(TokenProblem.MALFORMED)
    encoded = token.partition(".")[0]
    with contextlib.suppress(ValueError):  # binascii.Error and ValidationError are both ValueErrors
        return _Header.model_validate_json(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    _refuse(TokenProblem.MALFORMED)


def _registry(algorithm: str) -> JWSRegistry:
    """What joserfc verifies with: ``algorithm`` alone, and headers up to ``MAX_HEADER_SIZE`` bytes."""
    registry = JWSRegistry(algorithms=[algorithm])
    registry.max_header_length = MAX_HEADER_SIZE
    return registry


def _numeric_date(claims: Mapping[str, object], name: str) -> float | None:
    """The time claim ``name``, if there: a finite number of seconds (``true`` is no number), else refused."""
    match claims.get(name):
        case None:
            return None
        case bool():
            _refuse(TokenProblem.CLAIMS)
        case int() as seconds:
            return seconds
        case float() as seconds if math.isfinite(seconds):
            return seconds
        case _:
            _refuse(TokenProblem.CLAIMS)


def project_roles_claim(project: str) -> str:
    """The claim of the roles granted in ``project``: ``{role: {organization id: domain}}``.

    The only claim roles are read from, for the project that is the tokens' audience. Zitadel's generic
    ``urn:zitadel:iam:org:project:roles`` gathers the roles of every project the token was asked for, and a
    role of another project, though named alike, was granted for something else: neither counts.
    """
    return f"urn:zitadel:iam:org:project:{project}:roles"


def _granted_roles(claims: Mapping[str, object], name: str, organization: str) -> Iterator[str]:
    """The roles the claim ``name`` grants in ``organization``."""
    roles = _ROLES.validate_python(claims.get(name, {}))
    yield from (role for role, organizations in roles.items() if organization in organizations)


@final
class TokenVerifier:
    """Turns a bearer token into the ``Principal`` it proves, or refuses it (see the module)."""

    __slots__ = ("_clock", "_keys", "_policy", "_roles_claim")

    def __init__(
        self, policy: TokenPolicy, *, keys: PublishedKeys | None = None, clock: Callable[[], float] = time
    ) -> None:
        self._policy = policy
        self._keys = PublishedKeys(policy) if keys is None else keys
        self._clock = clock
        self._roles_claim = project_roles_claim(policy.audience)

    async def verify(self, token: str) -> Principal:
        """The principal ``token`` proves, else ``InvalidTokenError`` (or ``TokenKeysUnavailableError``)."""
        header = _header_of(token)
        if header.typ is not None and header.typ.lower() not in ACCESS_TOKEN_TYPES:
            _refuse(TokenProblem.WRONG_KIND)
        if header.alg not in self._policy.algorithms:
            _refuse(TokenProblem.ALGORITHM)
        key = await self._keys.key(header.kid)
        if key is None:
            _refuse(TokenProblem.UNKNOWN_KEY)
        claims = self._signed_claims(token, key, header.alg)
        self._check(claims)
        return self._principal(claims)

    @staticmethod
    def _signed_claims(token: str, key: Key, algorithm: str) -> dict[str, object]:
        """The claims of ``token`` once its signature verifies with ``key``."""
        try:
            decoded = jwt.decode(token, key, registry=_registry(algorithm))
        except (JoseError, ValueError, TypeError) as error:
            problem = _problem_of(error)
        else:
            return _valid(_OBJECT, decoded.claims, TokenProblem.MALFORMED)
        _refuse(problem)

    def _check(self, claims: Mapping[str, object]) -> None:
        """Refuse claims of another issuer or audience, of another kind of token, expired or not yet valid."""
        if claims.get("iss") != self._policy.issuer:
            _refuse(TokenProblem.ISSUER)
        audiences = _valid(_AUDIENCES, claims.get("aud"), TokenProblem.AUDIENCE)
        if self._policy.audience not in ([audiences] if isinstance(audiences, str) else audiences):
            _refuse(TokenProblem.AUDIENCE)
        for name in ACCESS_TOKEN_CLAIMS:
            _valid(_IDENTIFIER, claims.get(name), TokenProblem.WRONG_KIND)
        if not ID_TOKEN_CLAIMS.isdisjoint(claims):
            _refuse(TokenProblem.WRONG_KIND)
        expires = _numeric_date(claims, "exp")
        not_before = _numeric_date(claims, "nbf")
        issued = _numeric_date(claims, "iat")
        if expires is None:
            _refuse(TokenProblem.CLAIMS)
        now, leeway = self._clock(), self._policy.leeway
        if now >= expires + leeway:
            _refuse(TokenProblem.EXPIRED)
        if any(moment is not None and moment > now + leeway for moment in (not_before, issued)):
            _refuse(TokenProblem.NOT_YET_VALID)

    def _principal(self, claims: Mapping[str, object]) -> Principal:
        with contextlib.suppress(ValidationError, InvalidSubjectError):
            organization = _PROVIDER_ORGANIZATION.validate_python(claims.get(ORGANIZATION_CLAIM))
            return Principal(
                subject=parse_subject(_TEXT.validate_python(claims.get("sub"))),
                organization=organization_of(organization),
                provider_organization=organization,
                roles=frozenset(_granted_roles(claims, self._roles_claim, organization)),
            )
        _refuse(TokenProblem.CLAIMS)
