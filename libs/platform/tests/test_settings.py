from typing import Final
lazy from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from ledger_platform.settings import LogFormat, ServiceSettings

URL = "postgresql+psycopg://app:s3cret@db:5432/ledger"
VALID: Final = {"LEDGER_SERVICE_NAME": "invoicing", "LEDGER_DATABASE_URL": URL}


class BillingSettings(ServiceSettings):
    invoice_prefix: str = "INV"


def set_environment(monkeypatch: pytest.MonkeyPatch, variables: Mapping[str, str]) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def problems(error: ValidationError) -> set[str]:
    """Where each problem is: a field, or the name of an environment variable nobody declared."""
    return {str(detail["loc"][0]) for detail in error.errors(include_input=False)}


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


def test_an_unknown_key_in_given_values_is_refused() -> None:
    with pytest.raises(ValidationError) as refused:
        ServiceSettings.model_validate({"service_name": "invoicing", "database_url": URL, "http_prot": 9000})
    assert problems(refused.value) == {"http_prot"}


def test_a_misspelled_variable_stops_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_HTTP_PROT": "9000"})
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert problems(refused.value) == {"LEDGER_HTTP_PROT"}


def test_every_problem_is_listed_at_once_and_no_value_is_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(
        monkeypatch,
        {
            "LEDGER_SERVICE_NAME": "invoicing",
            "LEDGER_DB_URL": URL,
            "LEDGER_LOGLEVEL": "staging",
            "LEDGER_HTTP_PROT": "9000",
        },
    )
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert problems(refused.value) == {
        "LEDGER_DB_URL",
        "LEDGER_LOGLEVEL",
        "LEDGER_HTTP_PROT",
        "database_url",
    }
    assert "s3cret" not in str(refused.value)


def test_a_subclass_accepts_the_variables_of_its_own_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_INVOICE_PREFIX": "BIL"})
    assert BillingSettings().invoice_prefix == "BIL"


def test_a_variable_is_unknown_to_a_class_that_does_not_declare_it(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_INVOICE_PREFIX": "BIL"})
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert problems(refused.value) == {"LEDGER_INVOICE_PREFIX"}


def test_a_subclass_refuses_a_misspelling_of_its_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_INVOICE_PREFX": "BIL"})
    with pytest.raises(ValidationError) as refused:
        BillingSettings()
    assert problems(refused.value) == {"LEDGER_INVOICE_PREFX"}


def test_variables_that_are_not_ledger_prefixed_are_not_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"HTTP_PROT": "x", "OTHER_LEDGER_NAME": "y", "LEDGERISH_NAME": "z"})
    assert ServiceSettings().service_name == "invoicing"


def test_variable_names_are_matched_without_regard_to_case(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(
        monkeypatch,
        {"ledger_service_name": "invoicing", "Ledger_Database_Url": URL, "ledger_http_port": "9000"},
    )
    settings = ServiceSettings()
    assert (settings.service_name, settings.http_port) == ("invoicing", 9000)


def test_a_misspelled_variable_is_refused_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    set_environment(monkeypatch, VALID | {"Ledger_Http_Prot": "9000"})
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert {name.lower() for name in problems(refused.value)} == {"ledger_http_prot"}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://app:s3cret@db/ledger",
        "postgresql+psycopg2://app:s3cret@db/ledger",
        "postgresql+asyncpg://app:s3cret@db/ledger",
        "mysql+pymysql://app:s3cret@db/ledger",
        "app:s3cret@db/ledger",
    ],
    ids=["no-driver", "psycopg2", "asyncpg", "mysql", "no-scheme"],
)
def test_the_database_url_must_use_the_psycopg_driver_and_stays_secret(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_DATABASE_URL": url})
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert problems(refused.value) == {"database_url"}
    assert "postgresql+psycopg://" in str(refused.value)
    assert "s3cret" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "nats-host:4222",
        "http://nats:4222",
        "ws://nats:443",
        "nats://",
        "nats://:4222",
        "nats://user:s3cret@nats:port",
        "nats://nats:70000",
        "nats://nats:0",
    ],
    ids=[
        "no-scheme",
        "http",
        "websocket",
        "no-host",
        "empty-host",
        "port-not-a-number",
        "port-too-big",
        "port-0",
    ],
)
def test_the_nats_url_must_name_a_nats_server_and_stays_unshown(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """A URL nats-py could not even parse fails the settings, not the connection: it can hold a password."""
    set_environment(monkeypatch, VALID | {"LEDGER_NATS_URL": url})
    with pytest.raises(ValidationError) as refused:
        ServiceSettings()
    assert problems(refused.value) == {"nats_url"}
    assert "nats://host[:port] or tls://host[:port]" in str(refused.value)
    assert "s3cret" not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    ["nats://nats:4222", "nats://localhost", "tls://app:s3cret@nats.internal:4443", "nats://[::1]:4222"],
)
def test_a_nats_or_tls_url_with_a_host_is_accepted(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    set_environment(monkeypatch, VALID | {"LEDGER_NATS_URL": url})
    assert ServiceSettings().nats_url == url
