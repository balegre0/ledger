from contextlib import asynccontextmanager
from typing import Final, TypedDict
lazy from collections.abc import AsyncGenerator

import httpx
from pydantic import TypeAdapter

from ledger_platform.runtime.app import App
from ledger_platform.runtime.registries import REGIMES, installed
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.runtime.registries import Discover, Discovered
lazy from ledger_testing.modules import ModuleUnderTest


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


@asynccontextmanager
async def serving(settings: Settings, discover: Discover = installed) -> AsyncGenerator[httpx.AsyncClient]:
    """The ``api`` app on ``settings``, started, and a client of it."""
    app = App("api", settings=settings, discover=discover).asgi()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as client:
            yield client


async def test_the_api_is_ready_on_a_database_with_identity_s_schema_at_head(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]) == (
        200,
        {"database": True, "schema:platform": True, "schema:identity": True, "identity:regimes": True},
    )


async def test_the_api_on_a_database_never_migrated_is_not_ready(
    fresh: Settings, module_under_test: ModuleUnderTest
) -> None:
    async with serving(fresh, module_under_test.discovering()) as client:
        response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["schema:identity"]) == (503, False)


async def test_the_api_on_a_database_the_migrate_app_migrated_is_ready(
    fresh: Settings, module_under_test: ModuleUnderTest
) -> None:
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    async with serving(fresh, module_under_test.discovering()) as client:
        response = await client.get("/readyz")
    assert (response.status_code, all(READINESS.validate_json(response.content)["checks"].values())) == (
        200,
        True,
    )


async def test_the_api_without_a_regime_is_not_ready_to_read_tax_ids(
    settings: Settings, module_under_test: ModuleUnderTest
) -> None:
    discover = module_under_test.discovering()

    def without_regimes(group: str) -> tuple[Discovered, ...]:
        """What the module needs, but no regime: no tax id could be read."""
        return () if group == REGIMES else tuple(discover(group))

    async with serving(settings, without_regimes) as client:
        response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["identity:regimes"]) == (503, False)
    assert readiness["checks"]["schema:identity"]
