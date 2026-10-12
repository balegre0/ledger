"""Configuration every service shares, from ``LEDGER_*`` environment variables.

Invalid or missing values stop the start with every problem listed. Secrets are ``SecretStr``: never in
``repr``, logs or errors.
"""

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogFormat(StrEnum):
    """How logs are written."""

    CONSOLE = "console"
    JSON = "json"


class ServiceSettings(BaseSettings):
    """Settings of one service process. Services subclass it to add their own.

    Every field can come from the environment, so none is an argument the constructor demands: a field
    without a default says ``Field(default=...)`` (required when validating, optional to the type checkers),
    which keeps ``ServiceSettings()`` a valid call.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="LEDGER_", extra="forbid", frozen=True, env_ignore_empty=True
    )

    service_name: str = Field(default=..., pattern=r"^[a-z][a-z0-9-]{1,30}$")
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    database_url: SecretStr = Field(default=...)
    """SQLAlchemy URL with the psycopg driver: ``postgresql+psycopg://user:password@host:5432/db``."""
    nats_url: str = "nats://localhost:4222"
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    grpc_port: Annotated[int, Field(ge=0, le=65535)] = 50051
    """0 binds any free port (tests)."""
    otlp_endpoint: str | None = None
    """OTLP/gRPC collector, e.g. ``http://otel-collector:4317``; traces are not exported without it."""
