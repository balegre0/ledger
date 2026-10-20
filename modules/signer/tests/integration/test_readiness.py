"""No client role's readiness depends on the signer (spec §3.7, ADR 0010): with the signer down, ready.

The ``api`` and the ``worker`` apps start with signer's client configured (an address nothing listens on,
every caller's token) and its channel open; their ``/readyz`` answers 200 with no check of the signer (the
worker's once its listener is up).
"""

from http import HTTPStatus
from typing import Final, TypedDict

import httpx
import pytest
from pydantic import TypeAdapter

from ledger_platform.runtime.app import App
from ledger_testing.waiting import eventually
lazy from ledger_platform.config.settings import Settings
lazy from ledger_platform.runtime import AppRole
lazy from ledger_testing.modules import ModuleUnderTest

TOKENS: Final = {
    "api": "api-token-0123456789abcdefghijklmnopqrstuv",
    "worker": "worker-token-0123456789abcdefghijklmnopq",
    "receiver": "receiver-token-0123456789abcdefghijklmn",
}


class Readiness(TypedDict):
    status: str
    checks: dict[str, bool]


READINESS: Final = TypeAdapter(Readiness)


@pytest.mark.parametrize("role", ["api", "worker"])
async def test_a_client_role_is_ready_while_the_signer_is_down(
    settings: Settings, module_under_test: ModuleUnderTest, monkeypatch: pytest.MonkeyPatch, role: AppRole
) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", "127.0.0.1:1")
    for caller, token in TOKENS.items():
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", token)
    served = App(role, settings=settings, discover=module_under_test.discovering()).asgi()
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=served)
        async with httpx.AsyncClient(transport=transport, base_url="http://ledger.test") as client:

            async def ready() -> bool:
                return (await client.get("/readyz")).status_code == HTTPStatus.OK

            await eventually(ready)
            response = await client.get("/readyz")
    readiness = READINESS.validate_json(response.content)
    assert (response.status_code, readiness["status"]) == (200, "ready")
    assert all(readiness["checks"].values())
    assert [name for name in readiness["checks"] if name.startswith("signer:")] == []
