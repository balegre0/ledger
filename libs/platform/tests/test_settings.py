import pytest
from pydantic import ValidationError

from ledger_platform.settings import LogFormat, ServiceSettings

URL = "postgresql+psycopg://app:s3cret@db:5432/ledger"


def test_settings_come_from_ledger_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    monkeypatch.setenv("LEDGER_LOG_FORMAT", "json")
    monkeypatch.setenv("LEDGER_GRPC_PORT", "0")
    settings = ServiceSettings()
    assert (settings.service_name, settings.log_format, settings.grpc_port) == (
        "invoicing",
        LogFormat.JSON,
        0,
    )
    assert settings.database_url.get_secret_value() == URL
    assert (settings.environment, settings.http_port, settings.otlp_endpoint) == ("local", 8000, None)


def test_the_database_url_never_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    settings = ServiceSettings()
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings.model_dump())


@pytest.mark.parametrize(
    "environment",
    [
        {"LEDGER_SERVICE_NAME": "Invoicing", "LEDGER_DATABASE_URL": URL},
        {"LEDGER_SERVICE_NAME": "invoicing"},
        {"LEDGER_SERVICE_NAME": "invoicing", "LEDGER_DATABASE_URL": URL, "LEDGER_HTTP_PORT": "70000"},
        {"LEDGER_SERVICE_NAME": "invoicing", "LEDGER_DATABASE_URL": URL, "LEDGER_LOG_LEVEL": "TRACE"},
    ],
    ids=["uppercase-name", "no-database", "port-out-of-range", "unknown-level"],
)
def test_invalid_settings_stop_the_start(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        ServiceSettings()


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    monkeypatch.setenv("LEDGER_NATS_URL", "")
    monkeypatch.setenv("LEDGER_OTLP_ENDPOINT", "")
    settings = ServiceSettings()
    assert (settings.nats_url, settings.otlp_endpoint) == ("nats://localhost:4222", None)


def test_an_empty_database_url_is_a_missing_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", "")
    with pytest.raises(ValidationError):
        ServiceSettings()


def test_settings_do_not_change_after_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SERVICE_NAME", "invoicing")
    monkeypatch.setenv("LEDGER_DATABASE_URL", URL)
    settings = ServiceSettings()
    field = "http_port"  # a variable: the type checkers rightly refuse a plain assignment
    with pytest.raises(ValidationError):
        setattr(settings, field, 9000)


def test_unknown_settings_are_refused() -> None:
    with pytest.raises(ValidationError):
        ServiceSettings.model_validate({"service_name": "invoicing", "database_url": URL, "http_prot": 9000})
