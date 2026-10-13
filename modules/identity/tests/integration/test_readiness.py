from contextlib import asynccontextmanager
from typing import Final, TypedDict
lazy from collections.abc import AsyncGenerator

import httpx
from pydantic import TypeAdapter

from ledger_platform.runtime.app import App
lazy from ledger_platform.config.settings import Settings


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


@asynccontextmanager
async def serving(settings: Settings) -> AsyncGenerator[httpx.AsyncClient]:
    """The ``api`` app on ``settings``, started, and a client of it."""
    app = App("api", settings=settings).asgi()
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
        {"database": True, "schema:platform": True, "schema:identity": True},
    )


async def test_the_api_on_a_database_never_migrated_is_not_ready(fresh: Settings) -> None:
    async with serving(fresh) as client:
        response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["schema:identity"]) == (503, False)


async def test_the_api_on_a_database_the_migrate_app_migrated_is_ready(fresh: Settings) -> None:
    App("migrate", settings=fresh).migrate()
    async with serving(fresh) as client:
        response = await client.get("/readyz")
    assert (response.status_code, all(READINESS.validate_json(response.content)["checks"].values())) == (
        200,
        True,
    )
