import logging
from importlib.metadata import version

import pytest
from fastapi import FastAPI
from granian.constants import Interfaces
from granian.log import LogLevels, configure_logging
from pydantic import TypeAdapter
from starlette.routing import Route

from ledger_app_worker import main
from ledger_platform.runtime import SERVER_LOGGERS
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


def serve(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """What ``main`` gives Granian (a fake that records it and serves at once)."""
    served: list[dict[str, object]] = []

    class FakeGranian:
        def __init__(self, target: str, **options: object) -> None:
            served.append({"target": target, **options})

        def serve(self) -> None:
            served.append({"served": True})

    monkeypatch.setattr(main, "Granian", FakeGranian)
    assert main.main() == 0
    assert served[1] == {"served": True}
    return served[0]


@pytest.mark.usefixtures("environment")
def test_main_serves_the_factory_with_granian_on_the_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    options = serve(monkeypatch)
    assert options["target"] == "ledger_app_worker.main:create_app"
    assert (options["factory"], options["interface"]) == (True, Interfaces.ASGI)
    assert (options["address"], options["port"], options["workers"]) == ("127.0.0.1", 8101, 1)
    assert options["workers_kill_timeout"] == main.WORKER_KILL_TIMEOUT
    # a worker that dies takes its process down: the orchestrator restarts it, with backoff
    assert options["respawn_failed_workers"] is False


@pytest.mark.usefixtures("environment")
def test_granian_logs_through_the_platform_and_writes_no_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    options = serve(monkeypatch)
    assert (options["log_access"], options["log_level"]) == (False, LogLevels.info)
    assert options["log_dictconfig"] == {
        "loggers": {name: {"handlers": [], "propagate": True, "level": "INFO"} for name in SERVER_LOGGERS}
    }


@pytest.mark.usefixtures("environment")
def test_a_granian_record_is_a_json_line_of_the_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LEDGER_LOG_FORMAT", "json")
    options = serve(monkeypatch)
    configure_logging(
        LogLevels.info, TypeAdapter(dict[str, object]).validate_python(options["log_dictconfig"])
    )
    logging.getLogger("_granian").info("Starting granian")
    record = TypeAdapter(dict[str, object]).validate_json(capsys.readouterr().out.strip().splitlines()[-1])
    assert (record["event"], record["service"]) == ("Starting granian", "ledger-worker")


def test_granian_waits_for_the_app_to_stop_before_it_kills_a_worker() -> None:
    assert STOP_BUDGET < main.WORKER_KILL_TIMEOUT < 40
