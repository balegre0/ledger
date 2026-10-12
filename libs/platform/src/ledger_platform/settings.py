"""Configuration every service shares, from ``LEDGER_*`` environment variables.

Invalid or missing values stop the start with every problem listed, and so does a ``LEDGER_*`` variable that
is not a setting: a misspelled name must never mean "silently running with the default". Secrets are
``SecretStr`` and errors never echo the input: a secret is not in ``repr``, logs or errors.
"""

import os
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, final, override
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
lazy from pydantic_settings import PydanticBaseSettingsSource

_DATABASE_URL_SCHEME: Final = "postgresql+psycopg://"
"""The chassis talks to PostgreSQL through SQLAlchemy's asynchronous psycopg driver, and only that one."""
_NATS_SCHEMES: Final = frozenset({"nats", "tls"})
"""How the chassis reaches NATS: its own protocol, in clear or over TLS (never a websocket)."""
_NO_HOST: Final = frozenset({None, "none"})
"""What nats-py takes for a URL without a host: none at all, or the word (``urlsplit`` lowercases hosts)."""


class LogFormat(StrEnum):
    """How logs are written."""

    CONSOLE = "console"
    JSON = "json"


class _UnsupportedDatabaseUrlError(ValueError):
    """The database URL names another driver (or none). It never repeats the URL: it holds a password."""

    def __init__(self) -> None:
        super().__init__(f"must start with {_DATABASE_URL_SCHEME}")


class _UnsupportedNatsUrlError(ValueError):
    """The NATS URL names no NATS server. It never repeats the URL: it can hold a password."""

    def __init__(self) -> None:
        super().__init__("must be nats://host[:port] or tls://host[:port]")


def _names_a_nats_server(url: str) -> bool:
    """Whether ``url`` has a NATS scheme, a host and, if any, a port from 1 to 65535.

    What nats-py itself refuses (``Client._parse_server_uri`` in 2.16: no host, the host ``none``, a port that
    is no number in range) fails before its client is set up, and a client in that state cannot even be
    closed: such a URL is refused here, with the settings.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # a port that is no number, or out of range
        return False
    return parts.scheme in _NATS_SCHEMES and parts.hostname not in _NO_HOST and port != 0


@final
class _FailClosedEnvSettingsSource(EnvSettingsSource):
    """The environment, except that it refuses what looks like a setting and is not one.

    Left alone, a misspelled variable (``LEDGER_HTTP_PROT``) is dropped and the service runs on a default.
    Here every variable under the prefix that is not a field of the class being loaded travels on under its
    own name, so the model's ``extra="forbid"`` reports all of them with the other problems. Names match the
    way pydantic-settings reads the environment (without regard to case unless ``case_sensitive`` is set).
    Variables nested with ``env_nested_delimiter`` are not declared by any field name and are not supported.
    """

    @override
    def __call__(self) -> dict[str, object]:
        values: dict[str, object] = super().__call__()
        values.update(self._undeclared())
        return values

    def _undeclared(self) -> dict[str, str | None]:
        """The prefixed variables no field reads, under the name the environment gave them."""
        declared = {
            env_name
            for name, field in self.settings_cls.model_fields.items()
            for _, env_name, _ in self._extract_field_info(field, name)
        }
        prefix = self._apply_case_sensitive(self.env_prefix)
        given = {self._apply_case_sensitive(name): name for name in os.environ}
        return {
            given[name]: value
            for name, value in self.env_vars.items()
            if name.startswith(prefix) and name not in declared
        }


class ServiceSettings(BaseSettings):
    """Settings of one service process. Services subclass it to add their own.

    Every field can come from the environment, so none is an argument the constructor demands: a field
    without a default says ``Field(default=...)`` (required when validating, optional to the type checkers),
    which keeps ``ServiceSettings()`` a valid call.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="LEDGER_",
        extra="forbid",
        frozen=True,
        env_ignore_empty=True,
        hide_input_in_errors=True,  # a validation error must never print a secret
    )

    service_name: str = Field(default=..., pattern=r"^[a-z][a-z0-9-]{1,30}$")
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr = Field(default=...)
    """SQLAlchemy URL with the psycopg driver: ``postgresql+psycopg://user:password@host:5432/db``."""
    nats_url: SecretStr = SecretStr("nats://localhost:4222")
    """``nats://host:port``, or ``tls://`` for TLS; credentials, if any, in the URL (hence a secret)."""
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    grpc_port: Annotated[int, Field(ge=0, le=65535)] = 50051
    """0 binds any free port (tests)."""
    otlp_endpoint: str | None = None
    """OTLP/gRPC collector, e.g. ``http://otel-collector:4317``; traces are not exported without it."""

    @field_validator("database_url")
    @classmethod
    def require_the_psycopg_driver(cls, url: SecretStr) -> SecretStr:
        """Refuse a URL for another driver now, not at the first query."""
        if not url.get_secret_value().startswith(_DATABASE_URL_SCHEME):
            raise _UnsupportedDatabaseUrlError
        return url

    @field_validator("nats_url")
    @classmethod
    def require_a_nats_server(cls, url: SecretStr) -> SecretStr:
        """Refuse a URL that names no NATS server now, not when the service connects."""
        if not _names_a_nats_server(url.get_secret_value()):
            raise _UnsupportedNatsUrlError
        return url

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """The environment is read by a source that refuses undeclared variables."""
        del env_settings
        return (
            init_settings,
            _FailClosedEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )
