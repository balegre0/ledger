"""invoicing is assembled by the ``api`` app and ready once its schema (and identity's) is at head."""

from typing import Final, TypedDict

from pydantic import TypeAdapter
lazy import httpx

from ledger_platform.runtime.app import App
from ledger_testing.migrations import tables_of
lazy from ledger_platform.config.settings import Settings
lazy from ledger_testing.modules import ModuleUnderTest


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


async def test_the_api_assembles_invoicing_and_is_ready_with_its_schema_at_head(
    api: App, client: httpx.AsyncClient
) -> None:
    assert "invoicing" in [manifest.package.name for manifest in api.modules]
    response = await client.get("/readyz")
    checks = READINESS.validate_json(response.content)["checks"]
    assert (response.status_code, checks["schema:invoicing"]) == (200, True)


async def test_the_migrate_app_brings_invoicing_s_schema_to_head_from_nothing(
    fresh: Settings, fresh_admin_url: str, module_under_test: ModuleUnderTest
) -> None:
    assert tables_of(fresh_admin_url, schema="invoicing") == set()
    App("migrate", settings=fresh, discover=module_under_test.discovering()).migrate()
    assert "alembic_version" in tables_of(fresh_admin_url, schema="invoicing")
