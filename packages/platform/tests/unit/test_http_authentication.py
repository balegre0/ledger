"""``Authentication`` (§10; Decision 14): one verifier per app, from the token settings."""

from contextlib import AsyncExitStack
from http import HTTPStatus
from typing import Annotated, Final

import httpx
import pytest
from fastapi import Depends, FastAPI
from prometheus_client import CollectorRegistry, generate_latest
from pydantic import SecretStr

from ledger_platform.auth.tokens import TokenPolicy, TokenSettingsMissingError
from ledger_platform.config.settings import Settings
from ledger_platform.http.authentication import Authentication
from ledger_testing.issuer import AUDIENCE, ISSUER, SUBJECT
lazy from ledger_kernel.access import Principal
lazy from ledger_testing.issuer import LocalIssuer

SETTINGS: Final = Settings(
    environment="test", database_url=SecretStr("postgresql+psycopg://a:b@db.invalid/l")
)


def served(authentication: Authentication) -> FastAPI:
    app = FastAPI()
    caller = authentication.caller

    @app.get("/me")
    async def me(principal: Annotated[Principal, Depends(caller)]) -> dict[str, str]:
        return {"subject": principal.subject}

    return app


async def test_configured_from_the_token_settings_it_proves_callers(local_issuer: LocalIssuer) -> None:
    settings = SETTINGS.model_copy(
        update={"token_issuer": ISSUER, "token_audience": AUDIENCE, "token_jwks_url": local_issuer.keys_url}
    )
    registry = CollectorRegistry()
    authentication = Authentication.of(settings, registry)
    assert authentication.configured
    transport = httpx.ASGITransport(served(authentication))
    async with AsyncExitStack() as stack:
        for resource in authentication.resources():
            await stack.enter_async_context(resource)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            proven = await client.get("/me", headers={"Authorization": f"Bearer {local_issuer.token()}"})
    assert (proven.status_code, proven.json()) == (HTTPStatus.OK, {"subject": SUBJECT})
    assert b"ledger_auth_keys_age_seconds" in generate_latest(registry)


async def test_without_token_settings_it_proves_nobody() -> None:
    authentication = Authentication.of(SETTINGS)
    assert not authentication.configured
    assert authentication.resources() == ()
    transport = httpx.ASGITransport(served(authentication))
    async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
        with pytest.raises(TokenSettingsMissingError, match="token_issuer, token_audience, token_jwks_url"):
            await client.get("/me", headers={"Authorization": "Bearer any"})


def test_some_token_settings_without_the_others_are_refused() -> None:
    partly = SETTINGS.model_copy(update={"token_audience": AUDIENCE})
    with pytest.raises(TokenSettingsMissingError, match="token_issuer, token_jwks_url"):
        Authentication.of(partly)
    with pytest.raises(TokenSettingsMissingError, match="token_issuer"):
        TokenPolicy.of_settings(partly)
    with pytest.raises(TokenSettingsMissingError, match="token_issuer, token_audience, token_jwks_url"):
        Authentication.of(SETTINGS, required=True)
