from importlib.metadata import version

import pytest
from fastapi import FastAPI
from granian.constants import Interfaces
from starlette.routing import Route

from ledger_app_worker import main
from ledger_platform.runtime.app import STOP_BUDGET, DirectUrlMissingError

URL = "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger"


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker's environment: no token settings (it serves no module's routes), a direct URL (LISTEN)."""
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "test")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    monkeypatch.setenv("LEDGER_DATABASE_DIRECT_URL", URL)
    monkeypatch.setenv("LEDGER_HTTP_PORT", "8101")


@pytest.mark.usefixtures("environment")
def test_the_factory_builds_the_worker_app_without_connecting() -> None:
    app = main.create_app()
    assert isinstance(app, FastAPI)
    assert (app.title, app.version) == ("ledger-worker", version("ledger-app-worker"))
    paths = {route.path for route in app.routes if isinstance(route, Route)}
    assert {"/livez", "/readyz", "/metrics"} <= paths
    assert not any(path.startswith("/v1/") for path in paths)


@pytest.mark.usefixtures("environment")
def test_a_worker_without_a_direct_url_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_DIRECT_URL")
    with pytest.raises(DirectUrlMissingError):
        main.create_app()


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
    assert served[0]["target"] == "ledger_app_worker.main:create_app"
    assert (served[0]["factory"], served[0]["interface"]) == (True, Interfaces.ASGI)
    assert (served[0]["address"], served[0]["port"], served[0]["workers"]) == ("127.0.0.1", 8101, 1)
    assert (served[0]["respawn_failed_workers"], served[0]["log_access"]) == (True, False)
    assert served[0]["workers_kill_timeout"] == main.WORKER_KILL_TIMEOUT
    assert served[1] == {"served": True}


def test_granian_waits_for_the_app_to_stop_before_it_kills_a_worker() -> None:
    assert STOP_BUDGET < main.WORKER_KILL_TIMEOUT < 40
