from typing import Final, TypedDict
lazy from collections.abc import Callable

import httpx
from pydantic import TypeAdapter

from ledger_identity.main import build
lazy from ledger_identity.settings import IdentitySettings


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


async def test_identity_is_ready_on_a_database_at_its_schema(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["migrations"]) == (200, True)
    assert all(readiness["checks"].values())


async def test_identity_on_a_database_without_its_schema_is_not_ready(
    fresh_database_url: str, settings_for: Callable[[str], IdentitySettings]
) -> None:
    built = build(settings_for(fresh_database_url))
    async with built.app.router.lifespan_context(built.app):
        transport = httpx.ASGITransport(app=built.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as client:
            response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["checks"]["migrations"]) == (503, False)
