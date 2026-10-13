from importlib.metadata import version

import pytest
from fastapi import FastAPI
from granian.constants import Interfaces
from starlette.routing import Route

from ledger_app_api import main
from ledger_platform.runtime.app import STOP_BUDGET


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "test")
    monkeypatch.setenv("LEDGER_DATABASE_URL", "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger")
    monkeypatch.setenv("LEDGER_TOKEN_ISSUER", "https://issuer.invalid")
    monkeypatch.setenv("LEDGER_TOKEN_AUDIENCE", "ledger")
    monkeypatch.setenv("LEDGER_TOKEN_JWKS_URL", "https://issuer.invalid/keys")
    monkeypatch.setenv("LEDGER_HTTP_PORT", "8100")


@pytest.mark.usefixtures("environment")
def test_the_factory_builds_the_api_app_without_connecting() -> None:
    app = main.create_app()
    assert isinstance(app, FastAPI)
    assert (app.title, app.version) == ("ledger-api", version("ledger-app-api"))
    assert "/v1/companies" in app.openapi()["paths"]
    assert {"/livez", "/readyz", "/metrics"} <= {
        route.path for route in app.routes if isinstance(route, Route)
    }


@pytest.mark.usefixtures("environment")
def test_main_serves_the_factory_with_granian_on_the_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[dict[str, object]] = []

    class FakeGranian:
        def __init__(self, target: str, **options: object) -> None:
            served.append({"target": target, **options})

        def serve(self) -> None:
            served.append({"served": True})

    monkeypatch.setattr(main, "Granian", FakeGranian)
    assert main.main() == 0
    assert served[0]["target"] == "ledger_app_api.main:create_app"
    assert (served[0]["factory"], served[0]["interface"]) == (True, Interfaces.ASGI)
    assert (served[0]["address"], served[0]["port"], served[0]["workers"]) == ("127.0.0.1", 8100, 1)
    assert (served[0]["respawn_failed_workers"], served[0]["log_access"]) == (True, False)
    assert served[0]["workers_kill_timeout"] == main.WORKER_KILL_TIMEOUT
    assert served[1] == {"served": True}


def test_granian_waits_for_the_app_to_stop_before_it_kills_a_worker() -> None:
    """The platform stops within its budget; Granian kills a worker only after it; the orchestrator's grace
    (40 s: compose ``stop_grace_period``, Kubernetes ``terminationGracePeriodSeconds``) is longer still."""
    assert STOP_BUDGET < main.WORKER_KILL_TIMEOUT < 40
