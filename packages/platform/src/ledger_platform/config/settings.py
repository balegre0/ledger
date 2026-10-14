"""Configuration from ``LEDGER_*`` environment variables: the platform's, and each package's own section.

``Settings`` is the one class of every app: one environment file serves them all. A module or a channel may
declare a settings section in its manifest (``[package] settings``, ``[[channels]] settings``): its variables
are ``LEDGER_<SECTION>_*`` (``section_prefix``), read by the ``PackageSettings`` model its assembly hands
over, which the app validates when it is made and gives to that package alone (§3.10). ``sync`` registers
each declared section as an entry point of group ``ledger.settings``: ``Settings`` reads the names of that
group (it imports nothing) and leaves the variables under those prefixes to their sections.

Invalid or missing values stop the start with every problem listed, and so does a ``LEDGER_*`` variable that
is not a setting: a misspelled name must never mean "silently running with the default". That holds for
``Settings`` (a variable under no declared section's prefix) and for each section (a variable under its prefix
that it does not declare). Secrets are ``SecretStr`` and errors never echo the input: a secret is not in
``repr``, logs or errors.
"""

import os
from enum import StrEnum
from importlib.metadata import entry_points
from typing import Annotated, ClassVar, Final, Literal, Self, final, override

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
lazy from pydantic_settings import PydanticBaseSettingsSource

SETTINGS_GROUP: Final = "ledger.settings"
"""The entry point group naming the installed packages' settings sections (``sync`` writes it)."""
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


def section_prefix(section: str) -> str:
    """The variables of the settings section ``section``: ``LEDGER_DO_ECF_`` for ``do_ecf``."""
    return f"LEDGER_{section.upper()}_"


def declared_sections() -> frozenset[str]:
    """The settings sections the installed packages declare (the names of group ``ledger.settings``)."""
    return frozenset(entry.name for entry in entry_points(group=SETTINGS_GROUP))


@final
class _FailClosedEnvSettingsSource(EnvSettingsSource):
    """The environment, except that it refuses what looks like a setting and is not one.

    Left alone, a misspelled variable (``LEDGER_HTTP_PROT``) is dropped and the app runs on a default.
    Here every variable under the prefix that is not a field of the class being loaded travels on under its
    own name, so the model's ``extra="forbid"`` reports all of them with the other problems. Names match the
    way pydantic-settings reads the environment (without regard to case unless ``case_sensitive`` is set).
    Variables nested with ``env_nested_delimiter`` are not declared by any field name and are not supported.
    Variables under one of ``sections`` (prefixes) are left to those sections' own classes.
    """

    def __init__(self, settings_cls: type[BaseSettings], *, sections: frozenset[str]) -> None:
        super().__init__(settings_cls)
        self._sections: Final = frozenset(self._apply_case_sensitive(prefix) for prefix in sections)

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
            if name.startswith(prefix) and name not in declared and not name.startswith(tuple(self._sections))
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


class _ClosedSettings(BaseSettings):
    """Settings read by a source that refuses the undeclared variables under their prefix (see the module)."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        env_ignore_empty=True,
        hide_input_in_errors=True,  # a validation error must never print a secret
    )

    @classmethod
    def sections(cls) -> frozenset[str]:
        """The prefixes of the variables this class leaves to the packages' sections: none of its own."""
        return frozenset()

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
            _FailClosedEnvSettingsSource(settings_cls, sections=cls.sections()),
            dotenv_settings,
            file_secret_settings,
        )


class PackageSettings(_ClosedSettings):
    """The base of a package's settings section (see the module).

    A subclass sets its prefix, ``model_config = SettingsConfigDict(env_prefix=section_prefix("<section>"))``
    (the app refuses another), and declares its fields with defaults where it can: the app validates the
    section when it is made, and a module or a channel reads it from its context
    (``context.section(<Model>)``).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict()
    """Its own prefix comes from the subclass; the rest is ``_ClosedSettings``'s (pydantic merges the two)."""


class Settings(_ClosedSettings):
    """Settings of every app process (Decision 14): one class, so one environment file serves every app.

    ``environment`` and ``database_url`` are required. Token settings are optional here; the ``api`` role
    requires them when it starts. Every field can come from the environment, so none is an argument the
    constructor demands (``Field(default=...)``: required when validating, optional to the type checkers).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix="LEDGER_")

    environment: Literal["local", "test", "staging", "production"] = Field(default=...)
    """Required: a process that forgot to say where it runs must not run with local defaults in production."""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr = Field(default=...)
    """Where units run, through a pooler if any: ``postgresql+psycopg://ledger_app:...@pooler/ledger``."""
    database_direct_url: SecretStr | None = None
    """Direct to PostgreSQL (§6.7): the worker's LISTEN connection and ``ledger-migrate``, which need it."""
    database_pool_size: Annotated[int, Field(ge=1, le=1000)] = 30
    """Connections the app's pool keeps for its units; the app refuses to start with fewer than its role may
    hold at once (its jobs' concurrency, its runners and some headroom)."""
    database_pool_overflow: Annotated[int, Field(ge=0, le=1000)] = 10
    """Connections the pool opens beyond its size for a burst, closed once they are given back."""
    database_pooled: bool = False
    """``database_url`` goes through a transaction pooler (PgBouncer or the managed equivalent, §6.7).

    psycopg then prepares no statement on it: the pooler hands the next transaction another session.
    """
    http_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    """The address the HTTP server binds: loopback for a run on a host; the image sets every interface."""
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8100
    """The api's port on a host; the worker run on a host sets 8101, and the image sets 8000 (inside a
    container). Never 8000 on the host: another project's."""
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
    def sections(cls) -> frozenset[str]:
        """The prefixes of every section an installed package declares: their variables are theirs."""
        return frozenset(section_prefix(section) for section in declared_sections())
