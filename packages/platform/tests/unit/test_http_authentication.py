"""``Authentication`` (§10; Decision 14): one verifier per app, from the token settings."""

from contextlib import AsyncExitStack
from http import HTTPStatus
from typing import Annotated, Final, final, override

import httpx
import pytest
from fastapi import Depends, FastAPI
from prometheus_client import CollectorRegistry, generate_latest
from pydantic import SecretStr

from ledger_kernel.tenancy import Mode
from ledger_platform.auth.tokens import TokenPolicy, TokenSettingsMissingError
from ledger_platform.config.settings import Settings
from ledger_platform.http.authentication import Authentication
from ledger_platform.http.problems import install_problem_handlers
from ledger_platform.http.rate import RateLimitedError
from ledger_platform.http.standing import Standing, StandingsBoundError, StandingsPort
from ledger_testing.issuer import AUDIENCE, ISSUER, SUBJECT, machine_claims
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
    assert len(authentication.resources()) == 1  # the client that fetches the provider's keys
    transport = httpx.ASGITransport(served(authentication))
    async with AsyncExitStack() as stack:
        for resource in authentication.resources():
            await stack.enter_async_context(resource)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            proven = await client.get("/me", headers={"Authorization": f"Bearer {local_issuer.token()}"})
    assert (proven.status_code, proven.json()) == (HTTPStatus.OK, {"subject": SUBJECT})
    assert b"ledger_auth_keys_age_seconds" in generate_latest(registry)


async def test_a_caller_is_held_to_the_rate_the_settings_give(local_issuer: LocalIssuer) -> None:
    settings = SETTINGS.model_copy(
        update={
            "token_issuer": ISSUER,
            "token_audience": AUDIENCE,
            "token_jwks_url": local_issuer.keys_url,
            "rate_limit_per_second": 0.001,
            "rate_limit_burst": 1,
        }
    )
    authentication = Authentication.of(settings)
    transport = httpx.ASGITransport(served(authentication))
    async with AsyncExitStack() as stack:
        for resource in authentication.resources():
            await stack.enter_async_context(resource)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            headers = {"Authorization": f"Bearer {local_issuer.token()}"}
            first = await client.get("/me", headers=headers)
            with pytest.raises(RateLimitedError):
                await client.get("/me", headers=headers)
    assert first.status_code == HTTPStatus.OK


async def test_without_token_settings_it_proves_nobody() -> None:
    authentication = Authentication.of(SETTINGS)
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


@final
class Stands(StandingsPort):
    """Every caller stands as ``standing``."""

    def __init__(self, standing: Standing) -> None:
        self.standing = standing

    @override
    async def of(self, principal: Principal) -> Standing:
        del principal
        return self.standing


def configured(local_issuer: LocalIssuer) -> Authentication:
    return Authentication.of(
        SETTINGS.model_copy(
            update={
                "token_issuer": ISSUER,
                "token_audience": AUDIENCE,
                "token_jwks_url": local_issuer.keys_url,
            }
        )
    )


async def called(authentication: Authentication, token: str) -> httpx.Response:
    app = served(authentication)
    install_problem_handlers(app)  # a refusal is answered as the app answers it
    transport = httpx.ASGITransport(app)
    async with AsyncExitStack() as stack:
        for resource in authentication.resources():
            await stack.enter_async_context(resource)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:
            return await client.get("/me", headers={"Authorization": f"Bearer {token}"})


async def test_with_no_standings_bound_a_person_is_proven_and_a_machine_is_refused(
    local_issuer: LocalIssuer,
) -> None:
    """Nobody knows which machines are API clients: the caller fails closed for a machine."""
    authentication = configured(local_issuer)
    person = await called(authentication, local_issuer.token())
    machine = await called(authentication, local_issuer.token(subject="4001", claims=machine_claims("4001")))
    assert person.status_code == HTTPStatus.OK
    assert (machine.status_code, "api_client_unregistered" in machine.text) == (HTTPStatus.FORBIDDEN, True)


async def test_the_caller_applies_the_standings_bound_to_it(local_issuer: LocalIssuer) -> None:
    authentication = configured(local_issuer)
    port = Stands(Standing(mode=Mode.TEST))
    authentication.bind_standings(port)
    machine = local_issuer.token(subject="4001", claims=machine_claims("4001"))
    assert (await called(authentication, machine)).status_code == HTTPStatus.OK
    port.standing = Standing(mode=Mode.TEST, revoked=True)
    revoked = await called(authentication, machine)
    assert (revoked.status_code, "api_client_revoked" in revoked.text) == (HTTPStatus.UNAUTHORIZED, True)


def test_standings_are_bound_once(local_issuer: LocalIssuer) -> None:
    authentication = configured(local_issuer)
    authentication.bind_standings(Stands(Standing()))
    with pytest.raises(StandingsBoundError):
        authentication.bind_standings(Stands(Standing()))
