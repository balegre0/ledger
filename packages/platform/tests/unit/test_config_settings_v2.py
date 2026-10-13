"""``Settings``: the one class every app reads its ``LEDGER_*`` environment with (Decision 14)."""

from typing import Final
lazy from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from ledger_platform.config.settings import LogFormat, Settings

URL: Final = "postgresql+psycopg://ledger_app:s3cret@pooler:5432/ledger"
DIRECT: Final = "postgresql+psycopg://ledger_app:d1rect@db:5432/ledger"
REQUIRED: Final = {"LEDGER_DATABASE_URL": URL, "LEDGER_ENVIRONMENT": "local"}


def environment(monkeypatch: pytest.MonkeyPatch, variables: Mapping[str, str]) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def problems(error: ValidationError) -> set[str]:
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
    assert (settings.token_issuer, settings.token_audience, settings.token_jwks_url) == (None, None, None)
    assert (settings.token_algorithms, settings.token_leeway_seconds) == (frozenset({"RS256"}), 60)


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


def test_an_unknown_ledger_variable_fails_the_start_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, {**REQUIRED, "LEDGER_NOPE": "1"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {"LEDGER_NOPE"}


@pytest.mark.parametrize("name", ["LEDGER_DATABASE_URL", "LEDGER_DATABASE_DIRECT_URL"])
def test_a_database_url_of_another_driver_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    environment(monkeypatch, {**REQUIRED, name: "postgresql://ledger_app:l3ak@db/ledger"})
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert problems(caught.value) == {name.removeprefix("LEDGER_").lower()}
    assert "l3ak" not in str(caught.value)


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
    for shown in (repr(settings), str(settings.model_dump())):
        assert "s3cret" not in shown
        assert "d1rect" not in shown
