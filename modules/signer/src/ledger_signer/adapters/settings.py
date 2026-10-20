"""signer's settings section (``LEDGER_SIGNER_*``), read by every app that assembles signer.

The client roles (``api``, ``worker``, ``receiver``) read where the signer is and their own token: without
them the signer's routes and ``SignerPort`` answer ``signer_unavailable`` (Decision 13). The signer's role
reads its callers' tokens (each, and the one it replaces during a rotation), its gRPC port, the backends it
offers, its KMS and its rules of time. Lists are comma separated (``LEDGER_SIGNER_BACKENDS=local,remote``).
Tokens are ``SecretStr``: never in a ``repr``, a log line or an error.
"""

from typing import Annotated, ClassVar, Final, Literal, assert_type
lazy from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict
lazy from pydantic import SecretStr

from ledger_platform.config.settings import PackageSettings, section_prefix
from ledger_platform.grpc.client import MAX_DEADLINE

type Caller = Literal["api", "worker", "receiver"]
"""Who may call the signer (spec §3.7): each holds a token of its own."""
type BackendName = Literal["local", "remote"]
"""A signing backend a deployment may offer (``BackendKind``'s words)."""

MIN_SIGNING_SKEW_SECONDS: Final = 300
"""The least skew the signer may allow: what a channel assumes when it takes a signature back.

A channel checks a signed document's moment against its own clock with a skew of its own (the DO channel's
``MAX_SIGNING_SKEW``, 300 s); a signer allowing less would refuse documents the channel still holds fresh."""
MAX_SIGNING_SKEW_SECONDS: Final = 900
"""The most skew the signer may allow: past it a document's moment says too little of when it was signed."""
MAX_DEADLINE_SECONDS: Final = 10.0
"""The longest a client waits for the signer (spec §3.7: deadlines set by the caller, at most 10 s)."""
MAX_IMPORT_DEADLINE_SECONDS: Final = MAX_DEADLINE
"""The longest an import may take: a bundle streams, so it gets the most any call between services may."""


class _RepeatedDaysError(ValueError):
    """Two notices on one day: a typo in the list, never two notices meant."""

    def __init__(self) -> None:
        super().__init__("each day is named once")


class SignerSettings(PackageSettings):
    """``LEDGER_SIGNER_*`` (see the module)."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix=section_prefix("signer"))

    # every client role (api, worker, receiver): where the signer is, and its own token (Decision 13)
    address: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    """``host:port`` of the signer's gRPC service, e.g. ``signer:50051``; no signer without it."""
    deadline_seconds: Annotated[float, Field(gt=0, le=MAX_DEADLINE_SECONDS)] = 5.0
    """How long a client waits for each call but an import."""
    import_deadline_seconds: Annotated[float, Field(gt=0, le=MAX_IMPORT_DEADLINE_SECONDS)] = (
        MAX_IMPORT_DEADLINE_SECONDS
    )
    """How long the api waits for an import: a bundle of up to 2 MiB streams through it."""
    # the signer role: its callers' tokens (Decision 12), its port, its backends, its KMS, its rules
    token_api: SecretStr | None = None
    token_worker: SecretStr | None = None
    token_receiver: SecretStr | None = None
    token_api_previous: SecretStr | None = None
    """The api's token being replaced, accepted by the signer alone until the rotation ends."""
    token_worker_previous: SecretStr | None = None
    token_receiver_previous: SecretStr | None = None
    grpc_port: Annotated[int, Field(ge=0, le=65535)] = 50051
    """The port the signer serves gRPC on: never published on a host. ``0``: any free one (the tests')."""
    backends: Annotated[frozenset[BackendName], NoDecode, Field(min_length=1)] = frozenset({"local"})
    """The signing backends this deployment offers; an import for another is refused."""
    kms: Literal["local-file"] = "local-file"
    """The KMS that wraps data keys: a key file, for a laptop and tests only (a cloud KMS comes later)."""
    kms_key_file: Path | None = None
    """The local-file KMS's master key; the signer's role requires it."""
    signing_skew_seconds: Annotated[int, Field(ge=MIN_SIGNING_SKEW_SECONDS, le=MAX_SIGNING_SKEW_SECONDS)] = (
        MIN_SIGNING_SKEW_SECONDS
    )
    """How far a document's moment and the signer's clock may differ, and how long past the moment the
    certificate must stay valid (Decision 5)."""
    signing_concurrency: Annotated[int, Field(ge=1, le=64)] = 4
    """Signatures computed at once (the RSA work runs in threads, bounded by this)."""
    expiry_notice_days: Annotated[tuple[Annotated[int, Field(ge=0, le=366)], ...], NoDecode] = (
        30,
        14,
        7,
        1,
        0,
    )
    """How many days before a certificate expires its company is told (``0``: the day it expires)."""

    @field_validator("backends", "expiry_notice_days", mode="before")
    @classmethod
    def split_commas(cls, value: object) -> object:
        """A list from the environment is comma separated (``local,remote``), each item trimmed."""
        return tuple(item.strip() for item in value.split(",")) if isinstance(value, str) else value

    @field_validator("expiry_notice_days")
    @classmethod
    def require_distinct_days(cls, days: tuple[int, ...]) -> tuple[int, ...]:
        """Refuse a day named twice."""
        if len(frozenset(days)) != len(days):
            raise _RepeatedDaysError
        return days

    def token_of(self, caller: Caller) -> SecretStr | None:
        """``caller``'s current token, if it is set."""
        match caller:
            case "api":
                return self.token_api
            case "worker":
                return self.token_worker
            case _:
                assert_type(caller, Literal["receiver"])
                return self.token_receiver

    def tokens_of(self, caller: Caller) -> tuple[SecretStr, ...]:
        """The tokens the signer accepts from ``caller``: its current one, then the one it replaces.

        None without a current token: a previous one alone is no rotation, and the signer refuses to start.
        """
        current = self.token_of(caller)
        if current is None:
            return ()
        match caller:
            case "api":
                previous = self.token_api_previous
            case "worker":
                previous = self.token_worker_previous
            case _:
                assert_type(caller, Literal["receiver"])
                previous = self.token_receiver_previous
        return (current,) if previous is None else (current, previous)
