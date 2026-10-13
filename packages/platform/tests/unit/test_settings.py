"""``Settings``: the one class every app reads its ``LEDGER_*`` environment with."""

from typing import Final
lazy from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from ledger_platform.config.settings import DATABASE_URL_SCHEME, LogFormat, Settings

URL: Final = "postgresql+psycopg://ledger_app:s3cret@pooler:5432/ledger"
DIRECT: Final = "postgresql+psycopg://ledger_app:d1rect@db:5432/ledger"
REQUIRED: Final = {"LEDGER_DATABASE_URL": URL, "LEDGER_ENVIRONMENT": "local"}


class SectionedSettings(Settings):
    """What a section of settings of a module's own looks like to the environment."""

    invoice_prefix: str = "INV"


def environment(monkeypatch: pytest.MonkeyPatch, variables: Mapping[str, str]) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def problems(error: ValidationError) -> set[str]:
    """Where each problem is: a field, or the name of an environment variable nobody declared."""
    return {str(detail["loc"][0]) for detail in error.errors(include_input=False)}


def test_settings_need_only_the_database_and_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    settings = Settings()
    assert settings.database_url.get_secret_value() == URL
    assert (settings.environment, settings.log_level, settings.log_format) == (
        "local",
        "INFO",
        LogFormat.CONSOLE,
    )
    assert (settings.database_direct_url, settings.http_port, settings.otlp_endpoint) == (None, 8000, None)
    assert settings.http_host == "127.0.0.1"  # a run on a host binds loopback; the image says otherwise
    assert (settings.token_issuer, settings.token_audience, settings.token_jwks_url) == (None, None, None)
    assert (settings.token_algorithms, settings.token_leeway_seconds) == (frozenset({"RS256"}), 60)


def test_settings_come_from_ledger_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch,
        REQUIRED
        | {
            "LEDGER_LOG_FORMAT": "json",
            "LEDGER_LOG_LEVEL": "WARNING",
            "LEDGER_HTTP_HOST": "::1",
            "LEDGER_HTTP_PORT": "9000",
        },
    )
    settings = Settings()
    assert (settings.log_format, settings.log_level, settings.http_port) == (LogFormat.JSON, "WARNING", 9000)
    assert settings.http_host == "::1"


def test_a_pooled_database_url_is_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_POOLED": "true"})
    assert Settings().database_pooled
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_POOLED": "false"})
    assert not Settings().database_pooled


def test_without_a_database_or_an_environment_the_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_DATABASE_URL", raising=False)
    monkeypatch.delenv("LEDGER_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"database_url", "environment"}


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_OTLP_ENDPOINT": "", "LEDGER_DATABASE_URL": ""})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"database_url"}


def test_settings_do_not_change_after_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    settings = Settings()
    field = "http_port"  # a variable: the type checkers rightly refuse a plain assignment
    with pytest.raises(ValidationError):
        setattr(settings, field, 9000)


def test_an_unknown_key_in_given_values_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate({"environment": "local", "database_url": URL, "http_prot": 9000})
    assert problems(caught.value) == {"http_prot"}


def test_an_unknown_ledger_variable_fails_the_start_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_HTTP_PROT": "9000"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_HTTP_PROT"}


def test_every_problem_is_listed_at_once_and_no_value_is_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch,
        {
            "LEDGER_ENVIRONMENT": "local",
            "LEDGER_DB_URL": URL,
            "LEDGER_LOGLEVEL": "x",
            "LEDGER_HTTP_PROT": "9000",
        },
    )
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_DB_URL", "LEDGER_LOGLEVEL", "LEDGER_HTTP_PROT", "database_url"}
    assert "s3cret" not in str(caught.value)


def test_a_subclass_accepts_the_variables_of_its_own_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFIX": "BIL"})
    assert SectionedSettings().invoice_prefix == "BIL"


def test_a_variable_is_unknown_to_a_class_that_does_not_declare_it(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFIX": "BIL"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_INVOICE_PREFIX"}


def test_a_subclass_refuses_a_misspelling_of_its_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_INVOICE_PREFX": "BIL"})
    with pytest.raises(ValidationError) as caught:
        SectionedSettings()
    assert problems(caught.value) == {"LEDGER_INVOICE_PREFX"}


def test_variables_that_are_not_ledger_prefixed_are_not_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"HTTP_PROT": "x", "OTHER_LEDGER_NAME": "y", "LEDGERISH_NAME": "z"})
    assert Settings().environment == "local"


def test_variable_names_are_matched_without_regard_to_case(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(
        monkeypatch, {"ledger_environment": "test", "Ledger_Database_Url": URL, "ledger_http_port": "9000"}
    )
    settings = Settings()
    assert (settings.environment, settings.http_port) == ("test", 9000)


def test_a_misspelled_variable_is_refused_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED | {"Ledger_Http_Prot": "9000"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert {name.lower() for name in problems(caught.value)} == {"ledger_http_prot"}


@pytest.mark.parametrize("name", ["LEDGER_DATABASE_URL", "LEDGER_DATABASE_DIRECT_URL"])
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
def test_a_database_url_of_another_driver_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, name: str, url: str
) -> None:
    environment(monkeypatch, {**REQUIRED, name: url})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {name.removeprefix("LEDGER_").lower()}
    assert DATABASE_URL_SCHEME in str(caught.value)
    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize(
    ("environment_name", "outcome"),
    [("staging", "refused"), ("production", "refused"), ("local", "accepted"), ("test", "accepted")],
)
def test_keys_fetched_in_clear_are_refused_beyond_a_laptop(
    monkeypatch: pytest.MonkeyPatch, environment_name: str, outcome: str
) -> None:
    environment(
        monkeypatch,
        {
            **REQUIRED,
            "LEDGER_ENVIRONMENT": environment_name,
            "LEDGER_TOKEN_JWKS_URL": "http://zitadel:8080/oauth/v2/keys",
        },
    )
    if outcome == "refused":
        with pytest.raises(ValidationError, match="https"):
            Settings()
    else:
        assert Settings().token_jwks_url == "http://zitadel:8080/oauth/v2/keys"


def test_https_keys_and_no_keys_are_fine_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_ENVIRONMENT": "production"})
    assert Settings().token_jwks_url is None
    monkeypatch.setenv("LEDGER_TOKEN_JWKS_URL", "https://auth.example.com/oauth/v2/keys")
    assert Settings().token_jwks_url == "https://auth.example.com/oauth/v2/keys"


@pytest.mark.parametrize("algorithms", ['["HS256"]', '["none"]', '["RS256", "HS512"]', "[]"])
def test_only_asymmetric_algorithms_can_be_configured(
    monkeypatch: pytest.MonkeyPatch, algorithms: str
) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_TOKEN_ALGORITHMS": algorithms})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"token_algorithms"}


@pytest.mark.parametrize("leeway", ["-1", "301"])
def test_the_leeway_is_at_most_five_minutes(monkeypatch: pytest.MonkeyPatch, leeway: str) -> None:
    environment(monkeypatch, REQUIRED | {"LEDGER_TOKEN_LEEWAY_SECONDS": leeway})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"token_leeway_seconds"}


def test_listen_url_is_the_direct_url_in_libpq_form(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, REQUIRED)
    listen = Settings().listen_url
    assert listen.get_secret_value() == "postgresql://ledger_app:s3cret@pooler:5432/ledger"
    assert "s3cret" not in f"{listen!r} {listen}"
    monkeypatch.setenv("LEDGER_DATABASE_DIRECT_URL", DIRECT)
    assert Settings().listen_url.get_secret_value() == "postgresql://ledger_app:d1rect@db:5432/ledger"


def test_secrets_never_show(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_DATABASE_DIRECT_URL": DIRECT})
    settings = Settings()
    for shown in (repr(settings), str(settings), settings.model_dump_json()):
        assert "s3cret" not in shown
        assert "d1rect" not in shown
