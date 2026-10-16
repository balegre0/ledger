"""The chassis's request authentication, shared by every module's routers (§10).

Made once per app from the token settings (Decision 14): the identity provider's keys are fetched by one HTTP
client, opened as a resource while the app runs, and one verifier checks every bearer token. Without any token
setting the app proves nobody: its ``caller`` refuses every token with ``TokenSettingsMissingError`` (an
internal error). An app that serves routes requires the settings (``required``) and refuses to start without
them, so only an app that serves no module's routes is made that way. Some token settings without the others
are a deployment's mistake, refused at once.
"""

from typing import Final, final, override
lazy from contextlib import AbstractAsyncContextManager

lazy from prometheus_client import CollectorRegistry

from ledger_platform.auth.keys import HttpKeys, PublishedKeys
from ledger_platform.auth.tokens import TokenPolicy, TokenSettingsMissingError, TokenVerifier
from ledger_platform.http.deps import Verifier, authenticated
from ledger_platform.http.rate import RateLimiter
lazy from ledger_kernel.access import Principal
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.http.deps import Caller

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
    """The caller dependency every module's routers declare, and the resources it needs while the app runs."""

    __slots__ = ("_keys", "caller")

    def __init__(self, caller: Caller, keys: HttpKeys | None) -> None:
        self.caller: Final = caller
        """The FastAPI dependency giving a route its ``Principal`` (``Depends(caller)``)."""
        self._keys = keys

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

    def resources(self) -> tuple[AbstractAsyncContextManager[object], ...]:
        """What the app opens while it runs: the client that fetches the provider's keys, if any."""
        return () if self._keys is None else (self._keys.opened(),)
