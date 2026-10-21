import logging
import logging.config
from importlib.metadata import version
lazy from pathlib import Path

import pytest
from granian.constants import Interfaces
from granian.log import LogLevels, configure_logging
from pydantic import SecretStr, TypeAdapter
from starlette.routing import Route
lazy from fastapi import FastAPI

from ledger_platform.config.settings import LogFormat, Settings
from ledger_platform.runtime import SERVER_LOGGERS, serve, serving, serving_logs
from ledger_platform.runtime.app import STOP_BUDGET, DirectUrlMissingError

URL = "postgresql+psycopg://ledger_app:x@127.0.0.1:1/ledger"
_RECORD = TypeAdapter(dict[str, object])


def test_the_server_s_loggers_have_no_handler_of_their_own_and_reach_the_app_s() -> None:
    assert serving_logs(Settings(environment="test", database_url=SecretStr(URL)), service="ledger-api") == {
        "loggers": {name: {"handlers": [], "propagate": True, "level": "INFO"} for name in SERVER_LOGGERS}
    }
    assert SERVER_LOGGERS == ("_granian", "granian.access")


def test_a_server_record_is_a_line_of_the_app_s_format(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(environment="test", database_url=SecretStr(URL), log_format=LogFormat.JSON)
    config = serving_logs(settings, service="ledger-api")
    # what Granian does with it: its own configuration, the "loggers" section replaced by ours
    logging.config.dictConfig({"version": 1, "disable_existing_loggers": False, **config})
    logging.getLogger("_granian").info("Starting granian")
    record = _RECORD.validate_json(capsys.readouterr().out.strip().splitlines()[-1])
    assert (record["event"], record["service"], record["logger"]) == (
        "Starting granian",
        "ledger-api",
        "_granian",
    )


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """What both served roles need: the api's token settings, the worker's direct URL, a host port, and the
    archive the workspace's channel reads (nothing is archived: no app starts)."""
    monkeypatch.setenv("LEDGER_ARCHIVE_BACKEND", "file")
    monkeypatch.setenv("LEDGER_ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("LEDGER_ENVIRONMENT", "test")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    monkeypatch.setenv("LEDGER_DATABASE_DIRECT_URL", URL)
    monkeypatch.setenv("LEDGER_TOKEN_ISSUER", "https://issuer.invalid")
    monkeypatch.setenv("LEDGER_TOKEN_AUDIENCE", "ledger")
    monkeypatch.setenv("LEDGER_TOKEN_JWKS_URL", "https://issuer.invalid/keys")
    monkeypatch.setenv("LEDGER_HTTP_PORT", "8101")


def paths(app: FastAPI) -> set[str]:
    return {route.path for route in app.routes if isinstance(route, Route)}


@pytest.mark.usefixtures("environment")
def test_the_api_factory_builds_the_api_app_without_connecting() -> None:
    app = serving.api()
    assert (app.title, app.version) == ("ledger-api", version("ledger-app-api"))
    assert "/v1/companies" in app.openapi()["paths"]
    assert {"/livez", "/readyz", "/metrics"} <= paths(app)


@pytest.mark.usefixtures("environment")
def test_the_worker_factory_builds_the_worker_app_without_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The workspace's DO channel needs, in the worker, the roots counterparties chain to: the deployment's
    (read when the worker starts, never while it is built)."""
    monkeypatch.setenv("LEDGER_DO_ECF_TRUSTED_ROOTS", str(tmp_path / "roots.pem"))
    app = serving.worker()
    assert (app.title, app.version) == ("ledger-worker", version("ledger-app-worker"))
    assert {"/livez", "/readyz", "/metrics"} <= paths(app)
    assert not any(path.startswith("/v1/") for path in paths(app))


@pytest.mark.usefixtures("environment")
def test_the_signer_factory_builds_the_signer_app_without_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The workspace's signer module is isolated in the signer's role: its app serves probes, no route.

    The signer module refuses to start without its callers' tokens and its KMS's key file: the deployment's.
    """
    for caller in ("api", "worker", "receiver"):
        monkeypatch.setenv(f"LEDGER_SIGNER_TOKEN_{caller.upper()}", f"{caller}-token-".ljust(40, "x"))
    key_file = tmp_path / "kms.key"
    key_file.write_bytes(bytes(range(32)))
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", str(key_file))
    app = serving.signer()
    assert (app.title, app.version) == ("ledger-signer", version("ledger-app-signer"))
    assert {"/livez", "/readyz", "/metrics"} <= paths(app)
    assert not any(path.startswith("/v1/") for path in paths(app))


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize("role", ["worker", "signer"])
def test_an_app_that_listens_without_a_direct_url_does_not_start(
    monkeypatch: pytest.MonkeyPatch, role: serving.ServedRole
) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_DIRECT_URL")
    factory = {"worker": serving.worker, "signer": serving.signer}[role]
    with pytest.raises(DirectUrlMissingError, match=role):
        factory()


def served(monkeypatch: pytest.MonkeyPatch, role: serving.ServedRole) -> dict[str, object]:
    """What ``serve`` gives Granian (a fake that records it and serves at once)."""
    calls: list[dict[str, object]] = []

    class FakeGranian:
        def __init__(self, target: str, **options: object) -> None:
            calls.append({"target": target, **options})

        def serve(self) -> None:
            calls.append({"served": True})

    monkeypatch.setattr(serving, "Granian", FakeGranian)
    assert serve(role) == 0
    assert calls[1] == {"served": True}
    return calls[0]


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize("role", ["api", "worker", "signer"])
def test_serve_hands_granian_the_role_s_factory_on_the_configured_address(
    monkeypatch: pytest.MonkeyPatch, role: serving.ServedRole
) -> None:
    options = served(monkeypatch, role)
    assert options["target"] == f"ledger_platform.runtime.serving:{role}"
    assert (options["factory"], options["interface"]) == (True, Interfaces.ASGI)
    assert (options["address"], options["port"], options["workers"]) == ("127.0.0.1", 8101, 1)
    assert options["workers_kill_timeout"] == serving.WORKER_KILL_TIMEOUT
    # a worker that dies takes its process down: the orchestrator restarts it, with backoff
    assert options["respawn_failed_workers"] is False


@pytest.mark.usefixtures("environment")
def test_granian_logs_through_the_platform_and_writes_no_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    options = served(monkeypatch, "worker")
    assert (options["log_access"], options["log_level"]) == (False, LogLevels.info)
    assert options["log_dictconfig"] == {
        "loggers": {name: {"handlers": [], "propagate": True, "level": "INFO"} for name in SERVER_LOGGERS}
    }


@pytest.mark.usefixtures("environment")
def test_a_granian_record_is_a_json_line_of_the_served_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LEDGER_LOG_FORMAT", "json")
    options = served(monkeypatch, "api")
    configure_logging(LogLevels.info, _RECORD.validate_python(options["log_dictconfig"]))
    logging.getLogger("_granian").info("Starting granian")
    record = _RECORD.validate_json(capsys.readouterr().out.strip().splitlines()[-1])
    assert (record["event"], record["service"]) == ("Starting granian", "ledger-api")


def test_granian_kills_a_worker_only_after_the_app_s_stop_and_before_the_orchestrator() -> None:
    """The kill timeout follows from the stop budget; the orchestrator's grace is longer still."""
    assert STOP_BUDGET < serving.WORKER_KILL_TIMEOUT < serving.ORCHESTRATOR_GRACE
    assert serving.WORKER_KILL_TIMEOUT == 30
