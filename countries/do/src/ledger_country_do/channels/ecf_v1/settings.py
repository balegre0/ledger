"""do-ecf-v1's settings section (``LEDGER_DO_ECF_*``), read by every app that assembles the channel.

- ``ECF_HOST``, ``FC_HOST``: DGII's hosts (its own by default). Another host serves a fake DGII in a test
  environment only: the live environment refuses any but DGII's (``DgiiHosts``).
- ``DGII_CONNECT_SECONDS`` (5), ``DGII_ATTEMPT_SECONDS`` (20), ``DGII_CALL_SECONDS`` (60): how long DGII is
  waited for, to connect, in all per attempt, and per call (its token, every attempt and the waits between
  them); each no longer than the next. An attempt may not outlast the settle delay (``SETTLE_DELAY``, 30 s):
  the assembly refuses one that would (``SettleTooShortError``).
- ``TRUSTED_ROOTS``: a PEM file of the certification authorities a counterparty's certificate must chain to
  (buyers' receipts, received documents); required by the worker, which reads it when it starts, and by the
  receiver. Its path and contents are never printed.
- ``TEST_SENDER_STANDINS``: comma-separated SHA-256 fingerprints of certificates DGII signs with as a sender
  during certification (Decision 17), honoured in ``testecf`` and ``certecf`` only; refused in production.
- ``RECEIVER_PUBLIC_URL``: where the receiver is reachable (``https://...``, no path), for the URLs each
  company registers with DGII; until it is set, every enrollment requires it (``receiver_url``).

A refusal names the option and what it must be, never its value.
"""

import re
from typing import Annotated, ClassVar, Final, Self
from urllib.parse import urlsplit
lazy from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from ledger_country_do.channels.ecf_v1.domain.environments import OFFICIAL_ECF_HOST, OFFICIAL_FC_HOST
from ledger_platform.config.settings import PackageSettings, section_prefix

SECTION: Final = "do_ecf"
"""The channel's settings section: its schema's name."""
MAX_CONNECT_SECONDS: Final = 60.0
MAX_ATTEMPT_SECONDS: Final = 120.0
MAX_CALL_SECONDS: Final = 600.0
FINGERPRINT: Final = re.compile(r"[0-9a-f]{64}", re.ASCII)
"""A certificate's SHA-256 fingerprint as the settings spell it: 64 lower-case hexadecimal digits."""
MAX_URL: Final = 2048


class _WaitsOutOfOrderError(ValueError):
    def __init__(self) -> None:
        super().__init__("dgii_connect_seconds <= dgii_attempt_seconds <= dgii_call_seconds")


class _FingerprintError(ValueError):
    def __init__(self) -> None:
        super().__init__("test_sender_standins must be SHA-256 fingerprints: 64 lower-case hex digits each")


class _ReceiverUrlError(ValueError):
    def __init__(self) -> None:
        super().__init__("receiver_public_url must be an https origin: no user, path, query or fragment")


def _is_origin(url: str) -> bool:
    """Whether ``url`` is ``https://host[:port]``: what a path is appended to."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(host)
        and "@" not in parts.netloc
        and (parts.path, parts.query, parts.fragment) == ("", "", "")
        and not url.endswith(("?", "#"))
    )


class DoEcfSettings(PackageSettings):
    """``LEDGER_DO_ECF_*`` (see the module)."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix=section_prefix(SECTION))

    ecf_host: Annotated[str, Field(min_length=1, max_length=MAX_URL)] = OFFICIAL_ECF_HOST
    fc_host: Annotated[str, Field(min_length=1, max_length=MAX_URL)] = OFFICIAL_FC_HOST
    dgii_connect_seconds: Annotated[float, Field(gt=0, le=MAX_CONNECT_SECONDS)] = 5.0
    dgii_attempt_seconds: Annotated[float, Field(gt=0, le=MAX_ATTEMPT_SECONDS)] = 20.0
    dgii_call_seconds: Annotated[float, Field(gt=0, le=MAX_CALL_SECONDS)] = 60.0
    trusted_roots: Path | None = None
    test_sender_standins: Annotated[frozenset[str], NoDecode] = frozenset()
    receiver_public_url: Annotated[str, Field(min_length=1, max_length=MAX_URL)] | None = None

    @field_validator("test_sender_standins", mode="before")
    @classmethod
    def split_commas(cls, value: object) -> object:
        """A list from the environment is comma separated, each item trimmed; an empty one is none."""
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("test_sender_standins")
    @classmethod
    def require_fingerprints(cls, standins: frozenset[str]) -> frozenset[str]:
        """Refuse anything but SHA-256 fingerprints (the value is not repeated)."""
        if not all(FINGERPRINT.fullmatch(standin) for standin in standins):
            raise _FingerprintError
        return standins

    @field_validator("receiver_public_url")
    @classmethod
    def require_an_origin(cls, url: str | None) -> str | None:
        """Refuse a receiver URL that is not an https origin (the value is not repeated)."""
        if url is not None and not _is_origin(url):
            raise _ReceiverUrlError
        return url

    @model_validator(mode="after")
    def waits_in_order(self) -> Self:
        """An attempt is no shorter than its connection, and a call no shorter than one attempt."""
        if not self.dgii_connect_seconds <= self.dgii_attempt_seconds <= self.dgii_call_seconds:
            raise _WaitsOutOfOrderError
        return self
