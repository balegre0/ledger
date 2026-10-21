"""``LEDGER_DO_ECF_*``: DGII's hosts and waits, the trusted roots, the test stand-ins, the receiver's URL."""

from typing import Final
lazy from pathlib import Path

import pytest
from pydantic import ValidationError

from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST
from ledger_country_do.channels.ecf_v1.settings import DoEcfSettings

FINGERPRINT: Final = "ab" * 32
SECRET: Final = "C:/secret/roots-of-the-operator.pem"


@pytest.fixture(autouse=True)
def bare(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``LEDGER_DO_ECF_*`` of the shell reaches a test unless it sets one."""
    for name in ("ECF_HOST", "FC_HOST", "TRUSTED_ROOTS", "TEST_SENDER_STANDINS", "RECEIVER_PUBLIC_URL"):
        monkeypatch.delenv(f"LEDGER_DO_ECF_{name}", raising=False)
    for wait in ("CONNECT", "ATTEMPT", "CALL"):
        monkeypatch.delenv(f"LEDGER_DO_ECF_DGII_{wait}_SECONDS", raising=False)


def test_by_default_dgii_s_own_hosts_and_waits_no_roots_no_stand_ins_no_receiver() -> None:
    settings = DoEcfSettings()
    assert (settings.ecf_host, settings.fc_host) == (OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST)
    assert (settings.dgii_connect_seconds, settings.dgii_attempt_seconds, settings.dgii_call_seconds) == (
        5.0,
        20.0,
        60.0,
    )
    assert (settings.trusted_roots, settings.test_sender_standins, settings.receiver_public_url) == (
        None,
        frozenset(),
        None,
    )


def test_the_section_is_read_from_its_own_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LEDGER_DO_ECF_ECF_HOST", "http://127.0.0.1:8471")
    monkeypatch.setenv("LEDGER_DO_ECF_DGII_ATTEMPT_SECONDS", "10")
    monkeypatch.setenv("LEDGER_DO_ECF_TRUSTED_ROOTS", str(tmp_path / "roots.pem"))
    monkeypatch.setenv("LEDGER_DO_ECF_TEST_SENDER_STANDINS", f" {FINGERPRINT} , {'cd' * 32},")
    monkeypatch.setenv("LEDGER_DO_ECF_RECEIVER_PUBLIC_URL", "https://receiver.example.do:8443")
    settings = DoEcfSettings()
    assert (settings.ecf_host, settings.dgii_attempt_seconds) == ("http://127.0.0.1:8471", 10.0)
    assert settings.trusted_roots == tmp_path / "roots.pem"
    assert settings.test_sender_standins == frozenset({FINGERPRINT, "cd" * 32})
    assert settings.receiver_public_url == "https://receiver.example.do:8443"


@pytest.mark.parametrize(
    ("waits", "rule"),
    [
        ({"dgii_connect_seconds": 30.0}, "dgii_connect_seconds <= dgii_attempt_seconds"),
        ({"dgii_attempt_seconds": 90.0}, "dgii_attempt_seconds <= dgii_call_seconds"),
        ({"dgii_connect_seconds": 0.0}, "greater than 0"),
        ({"dgii_call_seconds": 601.0}, "less than or equal to 600"),
    ],
)
def test_waits_are_positive_bounded_and_in_order(waits: dict[str, float], rule: str) -> None:
    with pytest.raises(ValidationError, match=rule):
        DoEcfSettings.model_validate(waits)


@pytest.mark.parametrize("standin", ["AB" * 32, "ab" * 31, "zz" * 32, SECRET])
def test_a_stand_in_is_a_sha256_fingerprint_and_a_refusal_never_repeats_it(standin: str) -> None:
    with pytest.raises(ValidationError, match="64 lower-case hex digits") as caught:
        DoEcfSettings.model_validate({"test_sender_standins": frozenset({standin})})
    assert standin not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://receiver.example.do",
        "https://receiver.example.do/do-ecf-v1",
        "https://user@receiver.example.do",
        "https://receiver.example.do?x=1",
        "https://receiver.example.do#top",
        "https://",
        "https://[::1",
    ],
)
def test_the_receiver_s_url_is_an_https_origin(url: str) -> None:
    with pytest.raises(ValidationError, match="must be an https origin"):
        DoEcfSettings.model_validate({"receiver_public_url": url})
