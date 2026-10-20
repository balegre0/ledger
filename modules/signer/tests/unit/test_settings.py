"""signer's settings section, ``LEDGER_SIGNER_*``: what every client role and the signer role read."""

from pathlib import Path
from typing import Final

import pytest
from pydantic import ValidationError

from ledger_platform.config.settings import section_prefix
from ledger_signer.adapters.settings import MIN_SIGNING_SKEW_SECONDS, SignerSettings

TOKEN: Final = "api-token-0123456789abcdefghijklmnopqrstuvwxyz"
WORKER_TOKEN: Final = "worker-token-0123456789abcdefghijklmnopqrstuvw"
RECEIVER_TOKEN: Final = "receiver-token-0123456789abcdefghijklmnopqrstu"


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``LEDGER_SIGNER_*`` of the machine running the tests reaches them."""
    for name in (
        "ADDRESS",
        "DEADLINE_SECONDS",
        "TOKEN_API",
        "TOKEN_WORKER",
        "TOKEN_RECEIVER",
        "TOKEN_API_PREVIOUS",
        "TOKEN_WORKER_PREVIOUS",
        "TOKEN_RECEIVER_PREVIOUS",
        "GRPC_PORT",
        "BACKENDS",
        "KMS",
        "KMS_KEY_FILE",
        "SIGNING_SKEW_SECONDS",
        "SIGNING_CONCURRENCY",
        "EXPIRY_NOTICE_DAYS",
    ):
        monkeypatch.delenv(f"LEDGER_SIGNER_{name}", raising=False)


def problems(caught: pytest.ExceptionInfo[ValidationError]) -> list[tuple[str, ...]]:
    return [tuple(str(part) for part in issue["loc"]) for issue in caught.value.errors()]


def test_the_section_reads_its_own_prefix_and_defaults_to_a_client_without_a_signer() -> None:
    settings = SignerSettings()
    assert SignerSettings.model_config.get("env_prefix") == section_prefix("signer") == "LEDGER_SIGNER_"
    assert (settings.address, settings.deadline_seconds, settings.grpc_port) == (None, 5.0, 50051)
    assert (settings.backends, settings.kms, settings.kms_key_file) == (
        frozenset({"local"}),
        "local-file",
        None,
    )
    assert (settings.signing_skew_seconds, settings.signing_concurrency) == (300, 4)
    assert settings.expiry_notice_days == (30, 14, 7, 1, 0)


def test_tokens_never_show(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_API", TOKEN)
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_WORKER_PREVIOUS", WORKER_TOKEN)
    settings = SignerSettings()
    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)
    assert WORKER_TOKEN not in repr(settings)
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", TOKEN)  # a secret pasted into the wrong variable
    with pytest.raises(ValidationError) as caught:
        SignerSettings()
    assert TOKEN not in str(caught.value)


def test_a_deadline_over_ten_seconds_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_DEADLINE_SECONDS", "10")
    assert SignerSettings().deadline_seconds == 10.0
    monkeypatch.setenv("LEDGER_SIGNER_DEADLINE_SECONDS", "10.5")
    with pytest.raises(ValidationError) as caught:
        SignerSettings()
    assert problems(caught) == [("deadline_seconds",)]


def test_an_import_has_its_own_deadline_of_at_most_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bundle of 2 MiB streams through the api: longer than a signature, never longer than any call."""
    assert SignerSettings().import_deadline_seconds == 30.0
    monkeypatch.setenv("LEDGER_SIGNER_IMPORT_DEADLINE_SECONDS", "12.5")
    assert SignerSettings().import_deadline_seconds == 12.5
    for refused in ("30.5", "0"):
        monkeypatch.setenv("LEDGER_SIGNER_IMPORT_DEADLINE_SECONDS", refused)
        with pytest.raises(ValidationError) as caught:
            SignerSettings()
        assert problems(caught) == [("import_deadline_seconds",)]


def test_backends_parse_from_a_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", "local, remote")
    assert SignerSettings().backends == frozenset({"local", "remote"})
    monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", "remote")
    assert SignerSettings().backends == frozenset({"remote"})


@pytest.mark.parametrize("given", ["local,vault", ",", "local,,remote"])
def test_an_unknown_or_empty_backend_is_refused(monkeypatch: pytest.MonkeyPatch, given: str) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_BACKENDS", given)
    with pytest.raises(ValidationError) as caught:
        SignerSettings()
    assert {location[0] for location in problems(caught)} == {"backends"}


def test_expiry_notice_days_parse_from_a_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_EXPIRY_NOTICE_DAYS", "60, 30,0")
    assert SignerSettings().expiry_notice_days == (60, 30, 0)


@pytest.mark.parametrize("given", ["30,-1", "30,thirty", "7,7", "400"])
def test_expiry_notice_days_are_distinct_days_of_a_year(monkeypatch: pytest.MonkeyPatch, given: str) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_EXPIRY_NOTICE_DAYS", given)
    with pytest.raises(ValidationError) as caught:
        SignerSettings()
    assert {location[0] for location in problems(caught)} == {"expiry_notice_days"}


def test_the_signing_skew_never_falls_below_what_a_channel_assumes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel accepts a signature back within its own skew (the DO channel's ``MAX_SIGNING_SKEW``, 300 s):
    a signer with a smaller one would refuse documents the channel still holds fresh."""
    assert MIN_SIGNING_SKEW_SECONDS == 300
    for accepted in ("300", "900"):
        monkeypatch.setenv("LEDGER_SIGNER_SIGNING_SKEW_SECONDS", accepted)
        assert SignerSettings().signing_skew_seconds == int(accepted)
    for refused in ("299", "901"):
        monkeypatch.setenv("LEDGER_SIGNER_SIGNING_SKEW_SECONDS", refused)
        with pytest.raises(ValidationError) as caught:
            SignerSettings()
        assert problems(caught) == [("signing_skew_seconds",)]


def test_token_of_names_each_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    assert SignerSettings().token_of("api") is None
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_API", TOKEN)
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_WORKER", WORKER_TOKEN)
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_RECEIVER", RECEIVER_TOKEN)
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_API_PREVIOUS", TOKEN + "-old")
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_WORKER_PREVIOUS", WORKER_TOKEN + "-old")
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_RECEIVER_PREVIOUS", RECEIVER_TOKEN + "-old")
    settings = SignerSettings()
    tokens = {caller: settings.token_of(caller) for caller in ("api", "worker", "receiver")}
    assert {caller: token.get_secret_value() for caller, token in tokens.items() if token is not None} == {
        "api": TOKEN,
        "worker": WORKER_TOKEN,
        "receiver": RECEIVER_TOKEN,
    }
    previous = (settings.token_api_previous, settings.token_worker_previous, settings.token_receiver_previous)
    assert [token.get_secret_value() for token in previous if token is not None] == [
        TOKEN + "-old",
        WORKER_TOKEN + "-old",
        RECEIVER_TOKEN + "-old",
    ]


def test_the_signer_s_own_settings_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_ADDRESS", "signer:50051")
    monkeypatch.setenv("LEDGER_SIGNER_GRPC_PORT", "50052")
    monkeypatch.setenv("LEDGER_SIGNER_KMS_KEY_FILE", "/var/lib/ledger-signer/kms.key")
    monkeypatch.setenv("LEDGER_SIGNER_SIGNING_CONCURRENCY", "8")
    settings = SignerSettings()
    assert (settings.address, settings.grpc_port, settings.signing_concurrency) == ("signer:50051", 50052, 8)
    assert settings.kms_key_file == Path("/var/lib/ledger-signer/kms.key")


def test_a_variable_under_the_prefix_that_is_no_setting_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SIGNER_TOKEN_PRINTER", WORKER_TOKEN)
    with pytest.raises(ValidationError) as caught:
        SignerSettings()
    assert WORKER_TOKEN not in str(caught.value)
    assert [location[0].upper() for location in problems(caught)] == ["LEDGER_SIGNER_TOKEN_PRINTER"]
