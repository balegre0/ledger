"""Configuration every app shares, from ``LEDGER_*`` environment variables.

``Settings`` is the one class of every app: one environment file serves them all.

Invalid or missing values stop the start with every problem listed, and so does a ``LEDGER_*`` variable that
is not a setting: a misspelled name must never mean "silently running with the default". Secrets are
``SecretStr`` and errors never echo the input: a secret is not in ``repr``, logs or errors.
"""

import os
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Self, final, override

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
lazy from pydantic_settings import PydanticBaseSettingsSource

DATABASE_URL_SCHEME: Final = "postgresql+psycopg://"
"""The chassis talks to PostgreSQL through SQLAlchemy's asynchronous psycopg driver, and only that one."""


class LogFormat(StrEnum):
    """How logs are written."""

    CONSOLE = "console"
    JSON = "json"


class _UnsupportedDatabaseUrlError(ValueError):
    """The database URL names another driver (or none). It never repeats the URL: it holds a password."""

    def __init__(self) -> None:
        super().__init__(f"must start with {DATABASE_URL_SCHEME}")


@final
class _FailClosedEnvSettingsSource(EnvSettingsSource):
    """The environment, except that it refuses what looks like a setting and is not one.

    Left alone, a misspelled variable (``LEDGER_HTTP_PROT``) is dropped and the app runs on a default.
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


TokenAlgorithm = Literal[
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"
]
"""Signature algorithms an app may accept: asymmetric only, so ``none`` and HMAC cannot be set.

An ordinary alias, not a ``type`` statement: the verifier reads its values at runtime (``get_args``), and the
value of a ``type`` alias is typed ``Any``, which the type checkers refuse."""

_HTTPS_ENVIRONMENTS: Final = frozenset({"staging", "production"})


class _InsecureKeysUrlError(ValueError):
    """Keys fetched in clear could be swapped on the way, and every token then forged."""

    def __init__(self) -> None:
        super().__init__("token_jwks_url must be https:// outside the local and test environments")


class Settings(BaseSettings):
    """Settings of every app process (Decision 14): one class, so one environment file serves every app.

    ``environment`` and ``database_url`` are required. Token settings are optional here; the ``api`` role
    requires them when it starts. Every field can come from the environment, so none is an argument the
    constructor demands (``Field(default=...)``: required when validating, optional to the type checkers).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="LEDGER_",
        extra="forbid",
        frozen=True,
        env_ignore_empty=True,
        hide_input_in_errors=True,  # a validation error must never print a secret
    )

    environment: Literal["local", "test", "staging", "production"] = Field(default=...)
    """Required: a process that forgot to say where it runs must not run with local defaults in production."""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr = Field(default=...)
    """Where units run, through a pooler if any: ``postgresql+psycopg://ledger_app:...@pooler/ledger``."""
    database_direct_url: SecretStr | None = None
    """Direct to PostgreSQL (§6.7): the worker's LISTEN connection and ``ledger-migrate``, which need it."""
    database_pooled: bool = False
    """``database_url`` goes through a transaction pooler (PgBouncer or the managed equivalent, §6.7).

    psycopg then prepares no statement on it: the pooler hands the next transaction another session.
    """
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    otlp_endpoint: str | None = None
    """OTLP/gRPC collector, e.g. ``http://otel-collector:4317``; traces are not exported without it."""
    token_issuer: str | None = Field(default=None, min_length=1, max_length=2048)
    """``iss`` of the tokens: the identity provider's issuer, e.g. ``https://auth.example.com``."""
    token_audience: str | None = Field(default=None, min_length=1, max_length=255)
    """A value ``aud`` must hold: the Zitadel project id of the platform's API."""
    token_jwks_url: str | None = Field(default=None, min_length=1, max_length=2048)
    """Where the provider publishes its keys: ``<issuer>/oauth/v2/keys`` for Zitadel."""
    token_algorithms: frozenset[TokenAlgorithm] = Field(default=frozenset({"RS256"}), min_length=1)
    token_leeway_seconds: Annotated[int, Field(ge=0, le=300)] = 60
    """How far the provider's clock and ours may disagree, for ``exp``, ``nbf`` and ``iat``."""

    @field_validator("database_url", "database_direct_url")
    @classmethod
    def require_the_psycopg_driver(cls, url: SecretStr | None) -> SecretStr | None:
        """Refuse a URL for another driver now, not at the first query."""
        if url is not None and not url.get_secret_value().startswith(DATABASE_URL_SCHEME):
            raise _UnsupportedDatabaseUrlError
        return url

    @model_validator(mode="after")
    def require_https_keys_beyond_a_laptop(self) -> Self:
        """Refuse keys fetched in clear in staging and production."""
        keys = self.token_jwks_url
        if self.environment in _HTTPS_ENVIRONMENTS and keys is not None and not keys.startswith("https://"):
            raise _InsecureKeysUrlError
        return self

    @property
    def listen_url(self) -> SecretStr:
        """The direct URL in libpq form (psycopg's LISTEN connection); the database URL when none is set.

        A secret like the URLs it comes from: it holds the password, so it never renders in a repr or a log.
        """
        url = (self.database_direct_url or self.database_url).get_secret_value()
        return SecretStr(url.replace(DATABASE_URL_SCHEME, "postgresql://", 1))

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
