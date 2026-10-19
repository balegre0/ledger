"""The chassis's request authentication, shared by every module's routers (§10).

Made once per app from the token settings (Decision 14): the identity provider's keys are fetched by one HTTP
client, opened as a resource while the app runs, and one verifier checks every bearer token. Without any token
setting the app proves nobody: its ``caller`` refuses every token with ``TokenSettingsMissingError`` (an
internal error). An app that serves routes requires the settings (``required``) and refuses to start without
them, so only an app that serves no module's routes is made that way. Some token settings without the others
are a deployment's mistake, refused at once.
"""

from contextlib import aclosing
from typing import Annotated, Final, final, override
lazy from collections.abc import AsyncGenerator
lazy from contextlib import AbstractAsyncContextManager

from fastapi import Depends
lazy from fastapi import Request
lazy from fastapi.security import HTTPAuthorizationCredentials
lazy from prometheus_client import CollectorRegistry

from ledger_kernel.access import PrincipalKind
from ledger_platform.auth.keys import HttpKeys, PublishedKeys
from ledger_platform.auth.tokens import TokenPolicy, TokenSettingsMissingError, TokenVerifier
from ledger_platform.http.deps import BEARER, Verifier
from ledger_platform.http.mode import MODE_KEY
from ledger_platform.http.proving import authenticated
from ledger_platform.http.rate import RateLimiter
from ledger_platform.http.standing import (
    NOTHING_KNOWN,
    ApiClientRevokedError,
    ApiClientUnregisteredError,
    StandingsBoundError,
    resolve,
)
from ledger_platform.http.versions import VERSION_HEADER, VERSION_KEY, VersionUnknownError, parse_version
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.http.deps import Caller
lazy from ledger_platform.http.standing import Standing, StandingsPort

_TOKEN_SETTINGS: Final = ("token_issuer", "token_audience", "token_jwks_url")


@final
class _Unconfigured(Verifier):
    """What an app without token settings verifies with: nothing is proven."""

    @override
    async def verify(self, token: str) -> Principal:
        del token
        raise TokenSettingsMissingError(_TOKEN_SETTINGS)


@final
class Authentication:
    """The caller dependency every module's routers declare, and the resources it needs while the app runs.

    ``caller`` is the only one a module gets: it proves the bearer token (401), holds the caller to its rate
    (429), then settles the request as its caller stands, through the ``StandingsPort`` a module provides
    (identity, bound once by the app's assembly, ``bind_standings``): an API client that was revoked is
    refused (401 ``api_client_revoked``), and a machine user that is no API client (403
    ``api_client_unregistered``); the mode an API client is bound to becomes the request's
    (``ledger_mode_of``); and the version the request speaks is settled: a ``Ledger-Version`` it names is read
    (400 ``version_unknown`` for one that does not exist, or for two), else the pins decide. The answer to
    every request, this one's or not, names its version (``create_app``). While no standings are bound nobody
    is known to be an API client, so every machine is refused (it fails closed) and a person speaks what it
    names, else the current version. The caller that only proves the token is the chassis's own and stays
    private: no route can skip the standings.
    """

    __slots__ = ("_keys", "_proven", "_standings", "caller")

    def __init__(self, proven: Caller, keys: HttpKeys | None) -> None:
        self._proven = proven
        self._keys = keys
        self._standings: StandingsPort | None = None
        self.caller: Final = self._standing_caller()
        """The FastAPI dependency giving a route its ``Principal`` (``Depends(caller)``)."""

    @classmethod
    def of(
        cls, settings: Settings, registry: CollectorRegistry | None = None, *, required: bool = False
    ) -> Authentication:
        """Authentication by the token settings; ``registry`` exports the age of the provider's keys.

        ``TokenSettingsMissingError`` names the token settings unset when some are set, or when ``required``.
        """
        given = (settings.token_issuer, settings.token_audience, settings.token_jwks_url)
        if given == (None, None, None) and not required:
            return cls(authenticated(_Unconfigured()), None)
        policy = TokenPolicy.of_settings(settings)
        keys = HttpKeys()
        verifier = TokenVerifier(policy, keys=PublishedKeys(policy, fetch=keys, registry=registry))
        limiter = RateLimiter(rate=settings.rate_limit_per_second, burst=settings.rate_limit_burst)
        return cls(authenticated(verifier, limiter), keys)

    def bind_standings(self, standings: StandingsPort) -> None:
        """How callers stand, from the module that keeps it: bound once (``StandingsBoundError`` again)."""
        if self._standings is not None:
            raise StandingsBoundError
        self._standings = standings

    async def _standing_of(self, principal: Principal) -> Standing:
        """How ``principal`` stands; nothing is known of anyone while no standings are bound."""
        standings = self._standings
        return NOTHING_KNOWN if standings is None else await standings.of(principal)

    def _standing_caller(self) -> Caller:
        proven, standing_of = self._proven, self._standing_of

        async def caller(
            request: Request,
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
        ) -> AsyncGenerator[Principal]:
            async with aclosing(proven(request, credentials)) as callers:
                principal = await anext(callers)
                known = await standing_of(principal)
                if known.revoked:
                    raise ApiClientRevokedError
                if principal.kind is PrincipalKind.MACHINE and known.mode is None:
                    raise ApiClientUnregisteredError
                sent = request.headers.getlist(VERSION_HEADER)
                if len(sent) > 1:
                    raise VersionUnknownError
                request.scope[VERSION_KEY] = resolve(parse_version(sent[0]) if sent else None, known)
                if known.mode is not None:
                    request.scope[MODE_KEY] = known.mode
                yield principal

        return caller

    def resources(self) -> tuple[AbstractAsyncContextManager[object], ...]:
        """What the app opens while it runs: the client that fetches the provider's keys, if any."""
        return () if self._keys is None else (self._keys.opened(),)
