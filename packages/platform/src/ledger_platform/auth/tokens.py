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

The claims named above are Zitadel's (``auth.providers.zitadel``); the keys are held by ``PublishedKeys``
(``auth.keys``), which fetches, keeps and refreshes them.

A refused token is an ``InvalidTokenError`` that names the problem in a word (``expired``, ``audience``...)
and is raised with no cause or context attached: neither the token nor its claims are ever echoed or
logged.
"""

import base64
import contextlib
import math
from dataclasses import dataclass
from enum import StrEnum
from time import time
from typing import Annotated, ClassVar, Final, Never, final, get_args
lazy from collections.abc import Callable, Mapping

import structlog
from joserfc import jwt
from joserfc.errors import BadSignatureError, InvalidKeyCurveError, JoseError, KeyParameterError
from joserfc.jws import JWSRegistry
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, TypeAdapter, ValidationError
lazy from joserfc.jwk import Key

from ledger_kernel.access import InvalidSubjectError, Principal, parse_subject
from ledger_kernel.errors import LedgerError, UnauthenticatedError
from ledger_platform.auth.keys import PublishedKeys
from ledger_platform.auth.providers.zitadel import (
    ACCESS_TOKEN_CLAIMS,
    ACCESS_TOKEN_TYPES,
    ID_TOKEN_CLAIMS,
    ORGANIZATION_CLAIM,
    granted_roles,
    organization_of,
    project_roles_claim,
)
from ledger_platform.config.options import InvalidOptionError, require_seconds
from ledger_platform.config.settings import TokenAlgorithm
lazy from ledger_platform.config.settings import Settings

_COMPONENT: Final = "token"
"""What this module's option errors name (``InvalidOptionError.component``)."""

ASYMMETRIC_ALGORITHMS: Final = frozenset[str](get_args(TokenAlgorithm))
"""Every algorithm a token may be verified with: the ones the settings offer, all of them asymmetric."""
MAX_TOKEN_LENGTH: Final = 8192
"""Longer than any access token Zitadel issues; a longer one is refused unread."""
MAX_LEEWAY: Final = 300
MAX_HEADER_SIZE: Final = 2048
"""The largest JOSE header verified, in bytes: room for a header that carries a key, so that such a token is
refused for its signature, not for its size (joserfc's own limit is 512)."""
_RS256: Final = frozenset({"RS256"})
_DOTS: Final = 2
"""A compact JWS has three parts: header, payload, signature."""
_logger = structlog.stdlib.get_logger(__name__)


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


class TokenSettingsMissingError(LedgerError):
    """Token settings an app needs are unset: a defect of the deployment, found at the start."""

    code: ClassVar[str] = "token_settings_missing"

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(f"the token settings {', '.join(missing)} are not set")


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
                raise InvalidOptionError(
                    component=_COMPONENT, option=option, requirement="not empty", value=value
                )
        if not self.algorithms or not self.algorithms <= ASYMMETRIC_ALGORITHMS:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="algorithms",
                requirement=f"some of {', '.join(sorted(ASYMMETRIC_ALGORITHMS))}",
                value=sorted(self.algorithms),
            )
        if not 0 <= self.leeway <= MAX_LEEWAY:
            raise InvalidOptionError(
                component=_COMPONENT,
                option="leeway",
                requirement=f"between 0 and {MAX_LEEWAY} seconds",
                value=self.leeway,
            )
        require_seconds(_COMPONENT, "keys_ttl", self.keys_ttl)
        require_seconds(_COMPONENT, "keys_max_age", self.keys_max_age, floor=self.keys_ttl)
        require_seconds(_COMPONENT, "refresh_floor", self.refresh_floor)
        require_seconds(_COMPONENT, "fetch_timeout", self.fetch_timeout)

    @classmethod
    def of_settings(cls, settings: Settings) -> TokenPolicy:
        """The policy an app's settings describe (Decision 14).

        ``TokenSettingsMissingError`` names the token settings that are unset.
        """
        issuer, audience, keys_url = settings.token_issuer, settings.token_audience, settings.token_jwks_url
        if issuer is None or audience is None or keys_url is None:
            given = {"token_issuer": issuer, "token_audience": audience, "token_jwks_url": keys_url}
            raise TokenSettingsMissingError(tuple(name for name, value in given.items() if value is None))
        return cls(
            issuer=issuer,
            audience=audience,
            keys_url=keys_url,
            algorithms=frozenset(settings.token_algorithms),
            leeway=settings.token_leeway_seconds,
        )


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
                roles=frozenset(granted_roles(claims, self._roles_claim, organization)),
            )
        _refuse(TokenProblem.CLAIMS)
